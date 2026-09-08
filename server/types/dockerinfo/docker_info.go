package dockerinfo

import "github.com/moby/moby/api/types/system"

// EngineIdentity describes the container engine detected behind the socket.
// Arcane speaks the Docker API, but the engine may be Docker or Podman (which
// exposes a Docker-compatible API); several behaviors and feature gates depend
// on which one it is.
type EngineIdentity struct {
	// Name is the normalized engine identifier: "docker", "podman", or "" if
	// it could not be determined.
	//
	// Required: true
	Name string `json:"name"`

	// Podman is true when the detected engine is Podman. Convenience flag for
	// the UI to gate Docker-only features (Swarm, BuildKit builds, image patch).
	//
	// Required: true
	Podman bool `json:"podman"`

	// CgroupVersion is the daemon-reported cgroup version ("1" or "2"), or ""
	// if unknown. Rootless Podman is cgroup v2 only.
	//
	// Required: true
	CgroupVersion string `json:"cgroupVersion"`
}

type Info struct {
	// Embedded system.Info from the Docker daemon.
	//
	// Required: true
	system.Info

	// Success indicates if the Docker daemon information was successfully retrieved.
	//
	// Required: true
	Success bool `json:"success"`

	// APIVersion is the API version of the Docker daemon.
	//
	// Required: true
	APIVersion string `json:"apiVersion"`

	// GitCommit is the Git commit hash of the Docker daemon.
	//
	// Required: true
	GitCommit string `json:"gitCommit"`

	// GoVersion is the Go version used to build the Docker daemon.
	//
	// Required: true
	GoVersion string `json:"goVersion"`

	// Os is the operating system the Docker daemon is running on.
	//
	// Required: true
	Os string `json:"os"`

	// Arch is the architecture the Docker daemon is running on.
	//
	// Required: true
	Arch string `json:"arch"`

	// BuildTime is the build time of the Docker daemon.
	//
	// Required: true
	BuildTime string `json:"buildTime"`

	// Engine identifies the container engine (Docker vs Podman) and its cgroup
	// version, used to gate engine-specific features and behavior.
	//
	// Required: true
	Engine EngineIdentity `json:"engine"`
}
