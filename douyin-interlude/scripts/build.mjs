import { build } from 'esbuild';
await build({entryPoints:['upstream/src/index.ts'],outfile:'upstream/lib/index.js',bundle:true,platform:'node',format:'cjs',target:'node20',packages:'external',legalComments:'inline'});
