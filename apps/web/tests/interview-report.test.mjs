import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { followUpOrdinal } from '../src/types/match-presentation.ts';
import { confidenceLabel, proposalCanDecide, reportGenerationStatus, reportModeLabel, REPORT_DIMENSION_LABELS } from '../src/types/interview-report.ts';

test('memory decisions require PENDING and confidence describes support, not probability', () => {
  assert.equal(proposalCanDecide({ status: 'PENDING' }), true);
  for (const status of ['APPROVED', 'CORRECTED', 'REJECTED']) assert.equal(proposalCanDecide({ status }), false);
  assert.match(confidenceLabel('SINGLE_EVALUATION'), /单次.*非概率/);
  assert.match(confidenceLabel('REPEATED_EVALUATIONS'), /多次.*非概率/);
  assert.doesNotMatch(confidenceLabel('unknown'), /%/);
});

test('follow-up ordinal is per question root and remains historical after later turns', () => {
  const q = (id, kind, parent = null) => ({id, question_index:id, question_kind:kind, parent_question_id:parent});
  const questions = [q(1,'NEXT'),q(2,'FOLLOW_UP',1),q(3,'NEXT'),q(4,'FOLLOW_UP',3),q(5,'DEEP_DIVE',4),q(6,'FOLLOW_UP',2)];
  assert.equal(followUpOrdinal(questions,2),1);
  assert.equal(followUpOrdinal(questions,4),1);
  assert.equal(followUpOrdinal(questions,5),2);
  assert.equal(followUpOrdinal(questions,6),2);
  assert.equal(followUpOrdinal(questions,3),undefined);
});

test('report generation state stays separate and Replay is clearly labeled', () => {
  assert.equal(reportGenerationStatus({ generation_status: 'FAILED', status: 'COMPLETED' }), 'FAILED');
  assert.match(reportModeLabel({ provider: 'replay' }), /Replay.*不是 Live/);
  assert.match(reportModeLabel(null, { mode: 'replay' }), /Replay.*不是 Live/);
  assert.match(reportModeLabel({ provider: 'configured', model: 'real' }), /Live 实时模型/);
  assert.equal(reportModeLabel({ provider: 'openai_compatible', model: 'deepseek-v4-pro-ga-260813' }), 'DeepSeek 实时模型');
  assert.deepEqual(Object.keys(REPORT_DIMENSION_LABELS), ['technical_correctness', 'coverage', 'depth', 'relevance', 'communication']);
});

test('memory approval retains an explicit in-page confirmation and cancellation', () => {
  const page = readFileSync(new URL('../src/pages/InterviewReportPage.vue', import.meta.url), 'utf8');
  const selection = page.slice(page.indexOf('async function decide('), page.indexOf('async function confirmDecision('));
  assert.match(selection, /decision.value = \{ proposal, action \}/);
  assert.doesNotMatch(selection, /await approveMemoryProposal|await rejectMemoryProposal|confirm\(/);
  const commit = page.slice(page.indexOf('async function confirmDecision('), page.indexOf('async function saveCorrection('));
  assert.match(commit, /proposalCanDecide/);
  assert.match(commit, /await approveMemoryProposal/);
  assert.match(page, /aria-label="确认记忆决定"/);
  assert.match(page, /@click="decision = null">取消/);
});
