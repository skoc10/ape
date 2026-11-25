package collect

import (
	"fmt"
	"regexp"
	"strings"
)

// Source-side cleanup: the first of two locks. The Control Plane masks again
// on arrival, but values removed here never leave the customer's cluster.

const lastAppliedAnnotation = "kubectl.kubernetes.io/last-applied-configuration"

// Annotations in which deployment tools keep a full copy of the manifest.
var manifestCopyAnnotations = []string{
	lastAppliedAnnotation,
	"kapp.k14s.io/original",
	"kopf.zalando.org/last-handled-configuration",
	"objectset.rio.cattle.io/applied",
}

var sensitiveName = `(?:pass(?:word|wd)?|secret|token|api[-_]?key|credential|private[-_]?key|auth)`

// --db-password=hunter2, -token=abc, PASSWORD=abc
var sensitiveAssignment = regexp.MustCompile(`(?i)^(-{0,2}[\w.-]*` + sensitiveName + `[\w.-]*=)(.+)$`)

// --db-password (value is the next argument)
var sensitiveFlag = regexp.MustCompile(`(?i)^-{1,2}[\w.-]*` + sensitiveName + `[\w.-]*$`)

// scheme://user:password@host
var urlCredentials = regexp.MustCompile(`([a-zA-Z][a-zA-Z0-9+.-]*://[^/\s:@]+:)([^@\s/]+)(@)`)

// PASSWORD=abc or "api-key: abc" somewhere inside a longer string (shell one-liners).
var inlineAssignment = regexp.MustCompile(`(?i)([\w.-]*` + sensitiveName + `[\w.-]*\s*[=:]\s*)([^\s'"&|;<>]+)`)

// "Authorization: Bearer abc": the scheme stays, the credential goes.
var authScheme = regexp.MustCompile(`(?i)\b((?:bearer|basic)\s+)([^\s'"]+)`)

func redacted(value string) string { return fmt.Sprintf("<redacted:len=%d>", len(value)) }

// Sanitize removes secret-bearing fields from one object, in place.
func Sanitize(obj map[string]any) {
	if metadata, ok := obj["metadata"].(map[string]any); ok {
		// Both can carry a full copy of the manifest, including literal env values.
		delete(metadata, "managedFields")
		if annotations, ok := metadata["annotations"].(map[string]any); ok {
			sanitizeAnnotations(annotations)
			if len(annotations) == 0 {
				delete(metadata, "annotations")
			}
		}
	}
	sanitizeStatus(obj)
	for _, spec := range podSpecs(obj) {
		for _, field := range []string{"containers", "initContainers", "ephemeralContainers"} {
			containers, _ := spec[field].([]any)
			for _, c := range containers {
				if container, ok := c.(map[string]any); ok {
					sanitizeContainer(container)
				}
			}
		}
	}
}

// podSpecs finds the pod spec inside a Pod or any workload that embeds a pod template.
func podSpecs(obj map[string]any) []map[string]any {
	spec, _ := obj["spec"].(map[string]any)
	if spec == nil {
		return nil
	}
	var found []map[string]any
	if _, ok := spec["containers"]; ok {
		found = append(found, spec) // Pod
	}
	if tpl := nested(spec, "template", "spec"); tpl != nil {
		found = append(found, tpl) // Deployment, StatefulSet, DaemonSet, Job
	}
	if tpl := nested(spec, "jobTemplate", "spec", "template", "spec"); tpl != nil {
		found = append(found, tpl) // CronJob
	}
	return found
}

func nested(m map[string]any, path ...string) map[string]any {
	for _, key := range path {
		next, ok := m[key].(map[string]any)
		if !ok {
			return nil
		}
		m = next
	}
	return m
}

// sanitizeAnnotations drops manifest copies and cleans the free-form rest.
func sanitizeAnnotations(annotations map[string]any) {
	for _, name := range manifestCopyAnnotations {
		delete(annotations, name)
	}
	for key, v := range annotations {
		value, ok := v.(string)
		if !ok {
			continue
		}
		// An embedded JSON document (node arguments, tool state, another manifest
		// copy) cannot be cleaned reliably and no rule needs it: only its size stays.
		if trimmed := strings.TrimSpace(value); strings.HasPrefix(trimmed, "{") || strings.HasPrefix(trimmed, "[") {
			annotations[key] = redacted(value)
			continue
		}
		annotations[key] = sanitizeText(value)
	}
}

// sanitizeStatus removes container termination messages: they are the tail of
// the container's log (or its termination file) and can hold anything it printed.
func sanitizeStatus(obj map[string]any) {
	status, _ := obj["status"].(map[string]any)
	if status == nil {
		return
	}
	for _, field := range []string{"containerStatuses", "initContainerStatuses", "ephemeralContainerStatuses"} {
		statuses, _ := status[field].([]any)
		for _, s := range statuses {
			entry, ok := s.(map[string]any)
			if !ok {
				continue
			}
			for _, state := range []string{"state", "lastState"} {
				terminated := nested(entry, state, "terminated")
				if message, ok := terminated["message"].(string); ok {
					terminated["message"] = redacted(message)
				}
			}
		}
	}
}

// sanitizeHandler cleans a probe or lifecycle handler: exec commands are
// treated like container args, HTTP header values are always dropped.
func sanitizeHandler(handler map[string]any) {
	if exec := nested(handler, "exec"); exec != nil {
		if command, ok := exec["command"].([]any); ok {
			exec["command"] = sanitizeArgs(command)
		}
	}
	if httpGet := nested(handler, "httpGet"); httpGet != nil {
		headers, _ := httpGet["httpHeaders"].([]any)
		for _, h := range headers {
			header, ok := h.(map[string]any)
			if !ok {
				continue
			}
			if value, ok := header["value"].(string); ok {
				header["value"] = redacted(value)
			}
		}
	}
}

func sanitizeContainer(container map[string]any) {
	for _, probe := range []string{"livenessProbe", "readinessProbe", "startupProbe"} {
		if handler := nested(container, probe); handler != nil {
			sanitizeHandler(handler)
		}
	}
	for _, hook := range []string{"postStart", "preStop"} {
		if handler := nested(container, "lifecycle", hook); handler != nil {
			sanitizeHandler(handler)
		}
	}
	if env, ok := container["env"].([]any); ok {
		for _, e := range env {
			entry, ok := e.(map[string]any)
			if !ok {
				continue
			}
			// Literal values are always dropped: there is no reliable way to tell a
			// secret from a setting. valueFrom references stay, they name no secret value.
			if value, ok := entry["value"].(string); ok {
				entry["value"] = redacted(value)
			}
		}
	}
	for _, field := range []string{"command", "args"} {
		if args, ok := container[field].([]any); ok {
			container[field] = sanitizeArgs(args)
		}
	}
}

func sanitizeArgs(args []any) []any {
	out := make([]any, len(args))
	maskNext := false
	for i, a := range args {
		arg, ok := a.(string)
		if !ok {
			out[i] = a
			maskNext = false
			continue
		}
		switch {
		case maskNext && !strings.HasPrefix(arg, "-"):
			out[i] = redacted(arg)
			maskNext = false
			continue
		case sensitiveFlag.MatchString(arg):
			maskNext = true
			out[i] = arg
			continue
		}
		maskNext = false
		if m := sensitiveAssignment.FindStringSubmatch(arg); m != nil {
			out[i] = m[1] + redacted(m[2])
			continue
		}
		out[i] = sanitizeText(arg)
	}
	return out
}

// sanitizeText masks credentials inside free text: URL passwords, name=value
// pairs with a sensitive name and Bearer/Basic credentials.
func sanitizeText(text string) string {
	text = replaceGroup(urlCredentials, text)
	text = replaceGroup(authScheme, text)
	return replaceGroup(inlineAssignment, text)
}

// replaceGroup redacts the second capture group of every match of re.
func replaceGroup(re *regexp.Regexp, text string) string {
	return re.ReplaceAllStringFunc(text, func(match string) string {
		m := re.FindStringSubmatch(match)
		if strings.HasPrefix(m[2], "<redacted:") {
			return match
		}
		return m[1] + redacted(m[2]) + strings.Join(m[3:], "")
	})
}
