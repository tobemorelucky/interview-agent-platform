import { test } from 'node:test';
import assert from 'node:assert/strict';
import { confidenceLabel, proposalCanDecide, reportGenerationStatus, reportModeLabel, REPORT_DIMENSION_LABELS } from '../src/types/interview-report.ts';

test('memory decisions require PENDING and confidence describes support, not probability', () => {
  assert.equal(proposalCanDecide({ status: 'PENDING' }), true);
  for (const status of ['APPROVED', 'CORRECTED', 'REJECTED']) assert.equal(proposalCanDecide({ status }), false);
  assert.match(confidenceLabel('SINGLE_EVALUATION'), /单次.*非概率/);
  assert.match(confidenceLabel('REPEATED_EVALUATIONS'), /多次.*非概率/);
  assert.doesNotMatch(confidenceLabel('unknown'), /%/);
});

test('report generation state stays separate and Replay is clearly labeled', () => {
  assert.equal(reportGenerationStatus({ generation_status: 'FAILED', status: 'COMPLETED' }), 'FAILED');
  assert.match(reportModeLabel({ provider: 'replay' }), /Replay.*不是 Live/);
  assert.match(reportModeLabel(null, { mode: 'replay' }), /Replay.*不是 Live/);
  assert.match(reportModeLabel({ provider: 'configured', model: 'real' }), /Live 实时模型/);
  assert.equal(reportModeLabel({ provider: 'openai_compatible', model: 'deepseek-v4-pro-ga-260813' }), 'DeepSeek 实时模型');
  assert.deepEqual(Object.keys(REPORT_DIMENSION_LABELS), ['technical_correctness', 'coverage', 'depth', 'relevance', 'communication']);
});
