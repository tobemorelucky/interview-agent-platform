// Static UI contracts; real browser/API coverage is recorded separately.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { userNavigation } from '../src/i18n/zh-CN.ts';
const page = readFileSync(new URL('../src/pages/PublicCareerPage.vue', import.meta.url), 'utf8');
test('public library uses shared navigation and unwrapped API client contract', () => {
  assert.ok(userNavigation.some(n => n.path === '/career'));
  assert.match(page, /\/public-career\/jobs/);
  assert.match(page, /\/public-career\/experiences/);
  assert.doesNotMatch(page, /\.data\.data/);
  assert.match(page, /get<never, Job\[\]>/);
});
test('library is read-only and hands off only public interview references', () => {
  assert.doesNotMatch(page, /client\.(post|patch|delete)/);
  assert.match(page, /company_id: job.company_id, job_id: job.job_id/);
  assert.match(page, /来源身份未标注/);
  assert.match(page, /noopener noreferrer/);
  assert.match(page, /未触发|不触发/);
});
