import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import ts from 'typescript';

// Compile real response examples against the actual generated client. Checking
// only existing UI usages cannot detect an incorrect but currently unused type.
test('execute response represents rewrite and numbering without unsafe fields', () => {
  const frontend = fileURLToPath(new URL('../../', import.meta.url));
  const probe = path.join(frontend, 'src/api/generated/core-client/response-contract-probe.ts');
  const source = `
import type { DocumentRewriteExecuteOpenAPIResponseData as Result } from './api';
const rewrite: Result = { artifact_id: 'revision', revision: 4, draft_version: 1 };
const numbering: Result = {
  artifact_id: 'revision', revision: 4, draft_version: 1,
  title: 'Title', representation: 'markdown', document: '# Title',
  numbering: { ordered_style: 'hierarchical', entries: {} }
};
declare const response: Result;
// @ts-expect-error Numbering fields require narrowing: rewrite omits them.
response.numbering.ordered_style;
if ('numbering' in response) {
  const style: string = response.numbering.ordered_style;
}
`;
  const options = { noEmit: true, strict: true, skipLibCheck: true, target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, moduleResolution: ts.ModuleResolutionKind.Bundler };
  const host = ts.createCompilerHost(options);
  const readFile = host.readFile;
  host.readFile = file => path.resolve(file) === probe ? source : readFile(file);
  const program = ts.createProgram([probe], options, host);
  const diagnostics = ts.getPreEmitDiagnostics(program).filter(d => !d.file || path.resolve(d.file.fileName) === probe);
  assert.deepEqual(diagnostics.map(d => `${d.code}: ${ts.flattenDiagnosticMessageText(d.messageText, '\n')}`), []);
});
