package collect

import (
	"fmt"
	"regexp"
	"strings"
)

// Source-side cleanup: the first of two locks. The Control Plane masks again
// on arrival, but values removed here never leave the customer's cluster.

const lastAppliedAnnotation = "kubectl.kubernetes.io/last-applied-configuration"

var sensitiveName = `(?:pass(?:word|wd)?|secret|token|api[-_]?key|credential|private[-_]?key|auth)`

// --db-password=hunter2, -token=abc, PASSWORD=abc
var sensitiveAssignment = regexp.MustCompile(`(?i)^(-{0,2}[\w.-]*` + sensitiveName + `[\w.-]*=)(.+)$`)

// --db-password (value is the next argument)
var sensitiveFlag = regexp.MustCompile(`(?i)^-{1,2}[\w.-]*` + sensitiveName + `[\w.-]*$`)

// scheme://user:password@host
var urlCredentials = regexp.MustCompile(`([a-zA-Z][a-zA-Z0-9+.-]*://[^/\s:@]+:)([^@\s/]+)(@)`)

func redacted(value string) string { return fmt.Sprintf("<redacted:len=%d>", len(value)) }

// Sanitize removes secret-bearing fields from one object, in place.
func Sanitize(obj map[string]any) {
	if metadata, ok := obj["metadata"].(map[string]any); ok {
		// Both can carry a full copy of the manifest, including literal env values.
		delete(metadata, "managedFields")
		if annotations, ok := metadata["annotations"].(map[string]any); ok {
			delete(annotations, lastAppliedAnnotation)
			if len(annotations) == 0 {
				delete(metadata, "annotations")
			}
		}
	}
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

func sanitizeContainer(container map[string]any) {
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
			arg = m[1] + redacted(m[2])
		}
		arg = urlCredentials.ReplaceAllStringFunc(arg, func(match string) string {
			m := urlCredentials.FindStringSubmatch(match)
			return m[1] + redacted(m[2]) + m[3]
		})
		out[i] = arg
	}
	return out
}
