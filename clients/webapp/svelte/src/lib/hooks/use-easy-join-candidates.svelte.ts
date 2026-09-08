import { tryCatch } from '#lib/utils/try-catch.js';
import { swarmService } from '#lib/services/swarm-service.js';
import { environmentStore } from '#lib/stores/environment.store.svelte.js';
import type { SwarmJoinCandidate } from '#lib/types/swarm.js';

export function useEasyJoinCandidates() {
	let candidates = $state<SwarmJoinCandidate[]>([]);
	let managerEnvironmentId = $state<string | null>(null);
	let refreshVersion = $state(0);
	let requestVersion = 0;

	$effect(() => {
		const environmentId = environmentStore.selected?.id ?? null;
		void refreshVersion;
		managerEnvironmentId = environmentId;
		candidates = [];
		const currentRequest = ++requestVersion;
		if (!environmentId) return;

		void tryCatch(
			swarmService.getSwarmJoinCandidates(environmentId).then((result) => {
				if (currentRequest === requestVersion) candidates = result;
			})
		).then((result) => {
			if (result.error !== null) {
				if (currentRequest === requestVersion) candidates = [];
			} else {
				return result.data;
			}
		});
	});

	return {
		get managerEnvironmentId() {
			return managerEnvironmentId;
		},
		isCandidate(environmentId: string) {
			return candidates.some((candidate) => candidate.environmentId === environmentId);
		},
		refresh() {
			refreshVersion += 1;
		}
	};
}
