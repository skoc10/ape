// Package collect lists Kubernetes objects read-only, strips secrets at the
// source and streams them to the Control Plane in chunks of whole objects.
//
// It never reads Secret or ConfigMap objects: the agent's ClusterRole does not
// grant them, and they are not in the resource table below.
package collect
