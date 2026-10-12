const test = require('node:test');
const assert = require('node:assert/strict');
const sandbox = {module:{exports:{}}};
require('node:vm').runInNewContext(require('node:fs').readFileSync(__dirname+'/../studio/static/app.js','utf8'), sandbox);
const { actionState } = sandbox.module.exports;
test('publication lock disables approval, export and remote send even on approved clean draft', () => {
  const gates = actionState({status:'approved'}, false, true, true, false, false, true);
  assert.equal(gates.wordpress, false);
  assert.equal(gates.schedule, false);
  assert.equal(gates.download, false);
});
test('legacy approved flow remains available without lock', () => {
  assert.equal(actionState({status:'approved'}, false, true, true, false, false).wordpress, true);
});
