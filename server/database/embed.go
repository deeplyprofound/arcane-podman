// Package database embeds Arcane's goose SQL migrations so the backend can run
// them at startup. Migrations are dialect-specific: migrations/sqlite and
// migrations/postgres hold parallel sets, selected by the DB provider.
//
// Authoring lives here too (see the `just db` recipes); goose remains the
// runtime engine (backend/internal/database applies FS at boot).
package database

import "embed"

// FS holds the embedded migrations tree rooted at this module, i.e. paths are
// "migrations/<provider>/NNN_*.sql". The backend sub-roots it per provider.
//
//go:embed migrations
var FS embed.FS
