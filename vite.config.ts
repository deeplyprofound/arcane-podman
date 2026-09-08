import { defineConfig } from 'vite-plus';

export default defineConfig({
	fmt: {
		useTabs: true,
		singleQuote: true,
		trailingComma: 'none',
		printWidth: 100,
		sortPackageJson: true,
		ignorePatterns: [
			'.arcane.json',
			'.config/**',
			'.depot/**',
			'.devcontainer/**',
			'.github/**',
			'.vscode/**',
			'server/backend/**',
			'server/types/**',
			'server/database/**',
			'clients/cli/**',
			'pipes/deployment/**',
			'CHANGELOG.md',
			'docs/**',
			'cliff.toml',
			'depot.json',
			'pnpm-lock.yaml',
			'clients/webapp/svelte/.svelte-kit/**',
			'clients/webapp/svelte/build/**',
			'clients/webapp/svelte/messages/**',
			'clients/webapp/svelte/project.inlang/**',
			'clients/webapp/svelte/src/lib/paraglide/**',
			'pipes/gates/.auth/**',
			'pipes/gates/.bin/**',
			'pipes/gates/.report/**',
			'pipes/gates/test-results/**'
		],
		overrides: [
			{
				files: ['clients/webapp/svelte/**'],
				options: {
					printWidth: 130,
					sortPackageJson: false,
					svelte: true,
					sortTailwindcss: {
						stylesheet: './clients/webapp/svelte/src/routes/layout.css',
						attributes: ['class'],
						functions: ['clsx', 'cn'],
						preserveWhitespace: true
					}
				}
			}
		]
	},
	staged: {
		'clients/webapp/svelte/**/*': "sh -c 'just format frontend --check'",
		'{pipes/gates,email-templates}/**/*.{ts,tsx,js,jsx,mts,cts}': "sh -c 'just format js --check'",
		'{server/backend,server/types,server/database,clients/cli}/**/*': "sh -c 'just format go --check'"
	},
	test: {
		exclude: ['**/node_modules/**', 'pipes/gates/**'],
		passWithNoTests: true
	},
	lint: {
		jsPlugins: [{ name: 'vite-plus', specifier: 'vite-plus/oxlint-plugin' }],
		rules: { 'vite-plus/prefer-vite-plus-imports': 'error' },
		options: { typeAware: true, typeCheck: false }
	}
});
