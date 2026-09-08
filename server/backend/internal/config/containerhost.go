package config

import (
	"log/slog"
	"os"

	"github.com/getarcaneapp/arcane/backend/v2/pkg/libarcane/enginesocket"
)

// applyContainerHostDefaults auto-detects the container-engine socket when the
// user has not pinned DOCKER_HOST. Podman speaks the Docker API, so if no
// Docker socket is present but a Podman socket is, Arcane points at it and the
// rest of the Docker client path works unchanged. When DOCKER_HOST is set, or a
// Docker socket exists, the configured value is left untouched.
func applyContainerHostDefaults(cfg *Config) {
	_, explicit := os.LookupEnv("DOCKER_HOST")

	resolved, source := enginesocket.ResolveContainerHost(explicit, cfg.DockerHost, os.Stat, os.Getenv)
	if resolved != cfg.DockerHost {
		slog.Info("auto-detected container engine socket", "host", resolved, "source", source)
	}

	cfg.DockerHost = resolved
}
