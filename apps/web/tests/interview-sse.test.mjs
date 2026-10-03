import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SSEParser } from '../src/api/sse.ts';

const wire = ': heartbeat\r\nevent: status\r\ndata: {"stage":"evaluating"}\r\n\r\nevent: evaluation\ndata: {"score":4,"evaluation":"缓存"}\n\nevent: follow_up\ndata: {"question_id":2}\n\nevent: complete\ndata: {"overall_average":3.5}\n\nevent: done\ndata: {}\n\n';
test('every byte split preserves event names, UTF-8, order and payload', () => {
  const bytes = new TextEncoder().encode(wire);
  for (let split = 1; split < bytes.length; split++) {
    const parser = new SSEParser(), decoder = new TextDecoder();
    const events = [...parser.push(decoder.decode(bytes.slice(0, split), { stream: true })),
                    ...parser.push(decoder.decode(bytes.slice(split), { stream: true })), ...parser.push(decoder.decode())];
    parser.finish();
    assert.deepEqual(events.map(e => e.event), ['status', 'evaluation', 'follow_up', 'complete', 'done']);
    assert.equal(events[1].data.evaluation, '缓存');
  }
});
test('single-character chunks and multiline data', () => {
  const parser = new SSEParser();
  const events = [...'event: question\ndata: {"question_id":\ndata: 12}\n\n'].flatMap(c => parser.push(c));
  assert.deepEqual(events, [{ event: 'question', data: { question_id: 12 } }]);
});
test('invalid or truncated events are explicit failures', () => {
  assert.throws(() => new SSEParser().push('event: evaluation\ndata: bad\n\n'));
  const parser = new SSEParser();
  parser.push('event: evaluation\ndata: {"score":');
  assert.throws(() => parser.finish(), /incomplete/);
});
