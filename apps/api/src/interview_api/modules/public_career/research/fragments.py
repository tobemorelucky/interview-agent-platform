"""Deterministic source paragraphs with exact offsets and a reversible display map.

No semantic matching: normalization only removes presentation markup/collapses
whitespace. IDs include the Snapshot, bounds and raw excerpt. Comments are not
silently attributed to the author. Ambiguous text stays context, not questions.
"""
from hashlib import sha256
import re
import string
import unicodedata

from .evidence import EvidenceCatalog, EvidenceSpan, EvidenceValidationError

BOUNDARY = re.compile(r'^(?:全部评论|所有评论|相关推荐|推荐阅读|相关阅读|创作者周榜|all comments|related posts)\s*(?:\([^)]*\)|（[^）]*）)?\s*$', re.I)
INSTRUCTION = re.compile(r'ignore\s+(?:all\s+)?(?:previous|system)|system\s*prompt|忽略.{0,8}(?:指令|规则)|系统提示词|直接.{0,8}(?:发布|批准)|api[_ -]?key', re.I)
QUESTION = re.compile(r'[?？]|为什么|怎么|如何|哪些|什么|哪种|哪个|方法名', re.I)
IMPERATIVE = re.compile(r'^(?:请|让你|要求你)?(?:介绍|手写|设计|实现|说说|讲讲|解释|构建)')
REPORTED = re.compile(r'(?:又?问|考了?|追问).*(?:项目|论文|八股|基础|知识|技术|算法|数据库)', re.I)
PREFIX = re.compile(r'^\s*(?:#{1,6}\s+|\\?-\s*|•\s*|\\?\*\s+|\d+\\?[.、)）]\s*)')


def display_mapping(raw, start):
    prefix = PREFIX.match(raw)
    skip = prefix.end() if prefix else 0
    chars, offsets = [], []
    index = skip
    while index < len(raw):
        escaped = raw[index] == '\\' and index + 1 < len(raw) and raw[index + 1] in string.punctuation
        if escaped:
            index += 1
        if not escaped and (raw.startswith('**', index) or raw.startswith('~~', index)):
            index += 2
            continue
        if not escaped and raw[index] == '`':
            index += 1
            continue
        char = raw[index]
        value = ' ' if char.isspace() else unicodedata.normalize('NFKC', char)
        if value == ' ' and (not chars or chars[-1] == ' '):
            index += 1
            continue
        for part in value:
            chars.append(part)
            offsets.append(start + index)
        index += 1
    while chars and chars[-1] == ' ':
        chars.pop(); offsets.pop()
    return ''.join(chars), tuple(offsets)


class InterviewSourceFragments(EvidenceCatalog):
    def __init__(self, snapshots, *, max_chars=24000, max_fragments=150):
        self.spans, self.display, self.maps, self.kinds = {}, {}, {}, {}
        self.excluded = []
        snapshots = sorted(snapshots, key=lambda s: s.id)
        budget = max_chars // max(1, len(snapshots))
        for snapshot in snapshots:
            text = snapshot.content_text or ''
            for match in re.finditer(r'[^\r\n]+', text[:budget]):
                raw = match.group()
                shown, mapping = display_mapping(raw, match.start())
                if BOUNDARY.fullmatch(shown):
                    self.excluded.append(dict(snapshot_id=snapshot.id, start_char=match.start(), reason='comment_or_recommendation_boundary'))
                    break
                if not shown:
                    continue
                if INSTRUCTION.search(shown):
                    self.excluded.append(dict(snapshot_id=snapshot.id, start_char=match.start(), reason='untrusted_instruction'))
                    continue
                # Never expose an unbounded line or a budget-truncated paragraph
                # as if it were a complete question.
                if len(raw) > 2000 or (match.end() == budget and budget < len(text) and text[budget] not in '\r\n'):
                    self.excluded.append(dict(snapshot_id=snapshot.id, start_char=match.start(), reason='oversized_or_truncated_fragment'))
                    continue
                kind = 'CONTEXT'
                if not raw.lstrip().startswith('#'):
                    if QUESTION.search(shown) or IMPERATIVE.search(shown): kind = 'QUESTION'
                    elif REPORTED.search(shown): kind = 'REPORTED_TOPIC'
                key = 'ev_' + sha256(f'{snapshot.id}:{match.start()}:{match.end()}:{raw}'.encode()).hexdigest()[:24]
                self.spans[key] = EvidenceSpan(key, snapshot.id, snapshot.source_id, raw, match.start(), match.end())
                self.display[key], self.maps[key], self.kinds[key] = shown, mapping, kind
                if len(self.spans) >= max_fragments:
                    return

    def model_input(self):
        return [dict(fragment_id=key, text=self.display[key], source_kind=self.kinds[key]) for key in self.spans]

    def raw_range(self, key, start, end):
        self.resolve([key])
        mapping = self.maps[key]
        if not 0 <= start < end <= len(mapping):
            raise EvidenceValidationError('Invalid normalized source range')
        return mapping[start], mapping[end - 1] + 1

    def question_span(self, key, kind):
        span = self.resolve([key])[0]
        actual = self.kinds[key]
        if actual == 'CONTEXT' or (actual == 'REPORTED_TOPIC' and kind != 'REPORTED_TOPIC'):
            raise EvidenceValidationError(f'Fragment is not an exact reported question: fragment_id={key}; source_kind={actual}')
        return span
