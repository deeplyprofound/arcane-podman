package libarcane

import (
	"context"
	"strings"

	containertypes "github.com/moby/moby/api/types/container"
	systemtypes "github.com/moby/moby/api/types/system"
	"github.com/moby/moby/client"
)

// EngineCompatibilityInfo describes the container engine details Arcane uses to
// decide whether recreate-time HostConfig sanitization is required.
type EngineCompatibilityInfo struct {
	// Name is the normalized engine identifier, such as "docker" or "podman".
	Name string
	// CgroupVersion is the daemon-reported cgroup version, such as "1" or "2".
	CgroupVersion string
	// SELinuxEnabled reports whether the host enforces SELinux (Info
	// SecurityOptions contains "selinux"). Host binds then need :z/:Z relabeling.
	SELinuxEnabled bool
	// Rootless reports whether the engine runs rootless (Info SecurityOptions
	// contains "rootless"). Drives runtime-identity + port/ownership behavior.
	Rootless bool
}

// PrepareRecreateHostConfigForEngine clones hostConfig and removes recreate
// options that are known to be incompatible with the connected engine.
//
// The returned HostConfig is a shallow copy, so field reassignment is isolated
// from the caller's original value, but content-level mutation of shared slice
// or map fields is not. The boolean result reports whether the helper removed
// any incompatible fields. EngineCompatibilityInfo reports the daemon details
// used to make that decision.
func PrepareRecreateHostConfigForEngine(ctx context.Context, dockerClient *client.Client, hostConfig *containertypes.HostConfig) (*containertypes.HostConfig, bool, EngineCompatibilityInfo, error) {
	if hostConfig == nil {
		return nil, false, EngineCompatibilityInfo{}, nil
	}

	cloned := cloneContainerHostConfigInternal(hostConfig)
	if dockerClient == nil {
		return cloned, false, EngineCompatibilityInfo{}, nil
	}

	serverVersion, err := dockerClient.ServerVersion(ctx, client.ServerVersionOptions{})
	if err != nil {
		return cloned, false, EngineCompatibilityInfo{}, err
	}

	infoResult, err := dockerClient.Info(ctx, client.InfoOptions{})
	if err != nil {
		return cloned, false, EngineCompatibilityInfo{}, err
	}

	engineInfo := detectEngineCompatibilityInfoInternal(serverVersion, infoResult.Info)
	sanitized := sanitizeRecreateHostConfigInternal(cloned, engineInfo)

	return cloned, sanitized, engineInfo, nil
}

// IsPodman reports whether the detected engine is Podman.
func (e EngineCompatibilityInfo) IsPodman() bool {
	return strings.EqualFold(strings.TrimSpace(e.Name), "podman")
}

// SanitizeHostConfigForEngine removes/coerces HostConfig fields the detected
// engine rejects or ignores, mutating hostConfig in place and reporting whether
// anything changed. Today: drops MemorySwappiness on Podman + cgroup v2 (which
// rejects it). Callers pass the CACHED engine identity so this stays a pure,
// round-trip-free coercion on the container create/recreate/edit paths.
func SanitizeHostConfigForEngine(hostConfig *containertypes.HostConfig, engineInfo EngineCompatibilityInfo) bool {
	return sanitizeRecreateHostConfigInternal(hostConfig, engineInfo)
}

// EngineCompatibilityFrom derives the engine identity from an already-fetched
// ServerVersion + Info, without any additional daemon round-trip. Use this when
// the caller already holds both (e.g. the system-info handler).
func EngineCompatibilityFrom(version client.ServerVersionResult, info systemtypes.Info) EngineCompatibilityInfo {
	return detectEngineCompatibilityInfoInternal(version, info)
}

// DetectEngineInfo fetches the daemon's ServerVersion + Info and returns the
// engine identity (name + cgroup version). Callers should cache the result;
// engine identity is stable for the life of a connection.
func DetectEngineInfo(ctx context.Context, dockerClient *client.Client) (EngineCompatibilityInfo, error) {
	if dockerClient == nil {
		return EngineCompatibilityInfo{}, nil
	}

	serverVersion, err := dockerClient.ServerVersion(ctx, client.ServerVersionOptions{})
	if err != nil {
		return EngineCompatibilityInfo{}, err
	}

	infoResult, err := dockerClient.Info(ctx, client.InfoOptions{})
	if err != nil {
		return EngineCompatibilityInfo{}, err
	}

	return detectEngineCompatibilityInfoInternal(serverVersion, infoResult.Info), nil
}

func cloneContainerHostConfigInternal(hostConfig *containertypes.HostConfig) *containertypes.HostConfig {
	if hostConfig == nil {
		return nil
	}

	return new(*hostConfig)
}

func sanitizeRecreateHostConfigInternal(hostConfig *containertypes.HostConfig, engineInfo EngineCompatibilityInfo) bool {
	if hostConfig == nil {
		return false
	}

	if !strings.EqualFold(strings.TrimSpace(engineInfo.Name), "podman") || !isCgroupV2Internal(engineInfo.CgroupVersion) {
		return false
	}

	if hostConfig.MemorySwappiness == nil {
		return false
	}

	hostConfig.MemorySwappiness = nil
	return true
}

func detectEngineCompatibilityInfoInternal(version client.ServerVersionResult, info systemtypes.Info) EngineCompatibilityInfo {
	selinux, rootless := false, false
	for _, opt := range info.SecurityOptions {
		lo := strings.ToLower(opt)
		if strings.Contains(lo, "selinux") {
			selinux = true
		}
		if strings.Contains(lo, "rootless") {
			rootless = true
		}
	}
	return EngineCompatibilityInfo{
		Name:           detectEngineNameInternal(version, info),
		CgroupVersion:  strings.TrimSpace(info.CgroupVersion),
		SELinuxEnabled: selinux,
		Rootless:       rootless,
	}
}

// RelabelBindForEngine appends the SELinux shared-relabel suffix (:z) to a
// host-path bind ("src:dst[:mode]") when the engine is Podman on an SELinux
// host — otherwise the container is denied access to the bind. Named-volume
// binds (source is not an absolute path) and non-podman/non-selinux engines are
// returned unchanged; ":z"/":Z" already present is preserved (idempotent).
func RelabelBindForEngine(bind string, engineInfo EngineCompatibilityInfo) string {
	if !engineInfo.IsPodman() || !engineInfo.SELinuxEnabled {
		return bind
	}
	parts := strings.Split(bind, ":")
	// Only host-path binds (absolute source) need relabeling; named volumes don't.
	if len(parts) < 2 || !strings.HasPrefix(parts[0], "/") {
		return bind
	}
	mode := ""
	if len(parts) >= 3 {
		mode = parts[2]
	}
	if strings.Contains(mode, "z") || strings.Contains(mode, "Z") {
		return bind // already relabeled
	}
	if mode == "" {
		return parts[0] + ":" + parts[1] + ":z"
	}
	return parts[0] + ":" + parts[1] + ":" + mode + ",z"
}

func detectEngineNameInternal(version client.ServerVersionResult, info systemtypes.Info) string {
	candidates := []string{version.Platform.Name}
	for _, component := range version.Components {
		candidates = append(candidates, component.Name)
		for _, value := range component.Details {
			candidates = append(candidates, value)
		}
	}
	candidates = append(candidates, info.ServerVersion, info.OperatingSystem)

	for _, candidate := range candidates {
		if name := normalizeEngineNameInternal(candidate); name != "" {
			return name
		}
	}

	return ""
}

func normalizeEngineNameInternal(value string) string {
	normalized := strings.ToLower(strings.TrimSpace(value))
	switch {
	case strings.Contains(normalized, "podman"):
		return "podman"
	case strings.Contains(normalized, "docker"):
		return "docker"
	default:
		return ""
	}
}

func isCgroupV2Internal(cgroupVersion string) bool {
	normalized := strings.ToLower(strings.TrimSpace(cgroupVersion))
	normalized = strings.TrimPrefix(normalized, "v")
	return normalized == "2"
}
