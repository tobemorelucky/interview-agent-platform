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
QUESTION = re.compile(r'[?？]|为什么|怎么|如何|哪些|什么|哪种|哪个|方法名|有没有|是否|能否|吗\s*$|呢\s*$', re.I)
IMPERATIVE = re.compile(r'^(?:请|让你|要求你)?(?:介绍|手写|设计|实现|说说|讲讲|谈谈|聊聊|解释|构建)')
REPORTED = re.compile(r'(?:又?问|考了?|追问).*(?:项目|论文|八股|基础|知识|技术|算法|数据库)', re.I)
PREFIX = re.compile(r'^\s*(?:#{1,6}\s+|\\?-\s*|•\s*|\\?\*\s+|\d+\\?[.、)）]\s*)')
# A published 面经 lists questions as bare noun phrases ("spring 的启动流程",
# "分库分表"). Those lines are questions in context but carry no interrogative,
# so they are only promoted when they sit inside a list of other questions.
ANSWER_START = re.compile(r'^(?:答案|答|解析|参考回答|A)\s*[:：]', re.I)
CHROME = re.compile(r'浏览|评论|点赞|收藏|举报|展开|发私信|邀请|表情|话题|关注|分享|阅读|原文|编辑于|版权|转载|畅所欲言|图片|回复|快捷|私信', re.I)
STATUS = re.compile(r'收到|意向书|offer|感谢信|挂了|开奖|\boc\b|电话面|视频面|时长|分钟|\d{4}[.\-/]\d{1,2}|^[0-9\s.,]+$', re.I)
NARRATION = re.compile(r'我是|我叫|我在|我负责|我做|我收到|本人|楼主|小姐姐|然后|时候|觉得|感觉|其实|不过|希望|已经|顺利')
SECTION = re.compile(r'^(?:\d{1,2}\s*[.\-/]?\s*)?(?:[一二三四五1-5]\s*面|hr\s*面|笔试|面试复盘)', re.I)
# "## 1. 自我介绍" is an item in a numbered question list; the h1 page title
# ("快手 AI应用开发 二面") is not.
NUMBERED_HEADING = re.compile(r'^#{1,4}\s*\d+\s*[.、)）]')
LISTED_MAX = 80
# A reported question is a line, not a paragraph. Long prose that merely
# contains 是否/有没有 is the poster's explanation, never a question.
PROSE_MAX = 120
NEIGHBOUR = 3


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
    def __init__(self, snapshots, *, max_chars=24000, max_fragments=400):
        self.spans, self.display, self.maps, self.kinds = {}, {}, {}, {}
        self.excluded = []
        snapshots = sorted(snapshots, key=lambda s: s.id)
        budget = max_chars // max(1, len(snapshots))
        for snapshot in snapshots:
            text = snapshot.content_text or ''
            rows, section, in_answer = [], 0, False
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
                heading = raw.lstrip().startswith('#')
                if ANSWER_START.match(shown):
                    kind = 'ANSWER'
                elif shown.rstrip().endswith((':', '：')) and '?' not in shown and '？' not in shown \
                        and not IMPERATIVE.search(shown):
                    # "后端还要校验引用是否真实存在：" introduces an explanation.
                    kind = 'CONTEXT'
                elif '。' in shown and not shown.rstrip().endswith(('?', '？')):
                    # A sentence-terminated paragraph is prose, even when it
                    # happens to contain 是否 or 有没有.
                    kind = 'CONTEXT'
                elif QUESTION.search(shown) or IMPERATIVE.search(shown):
                    kind = 'QUESTION' if (len(shown) <= PROSE_MAX or shown.rstrip().endswith(('?', '？'))) else 'CONTEXT'
                elif REPORTED.search(shown):
                    kind = 'REPORTED_TOPIC'
                if kind == 'ANSWER':
                    in_answer = True
                elif kind == 'QUESTION' or heading:
                    # A new question or heading ends the previous answer block.
                    in_answer = False
                elif in_answer and kind == 'CONTEXT':
                    # Prose under "答案：" is the poster's own explanation.
                    kind = 'ANSWER'
                if SECTION.search(shown):
                    section += 1
                key = 'ev_' + sha256(f'{snapshot.id}:{match.start()}:{match.end()}:{raw}'.encode()).hexdigest()[:24]
                rows.append(dict(key=key, raw=raw, shown=shown, start=match.start(), end=match.end(),
                                 mapping=mapping, kind=kind, section=section, heading=heading,
                                 listed=heading and bool(NUMBERED_HEADING.match(raw))))
            self._promote_listed_questions(rows)
            for row in rows:
                self.spans[row['key']] = EvidenceSpan(row['key'], snapshot.id, snapshot.source_id, row['raw'], row['start'], row['end'])
                self.display[row['key']] = row['shown']
                self.maps[row['key']] = row['mapping']
                self.kinds[row['key']] = row['kind']
                if len(self.spans) >= max_fragments:
                    return

    @staticmethod
    def _promote_listed_questions(rows):
        """Promote bare topic lines that sit in a list of questions.

        Deterministic and local: inside an interview round ("一面"), or next to
        an explicit question, a short non-narrative line is how 面经 list what
        was asked. Page chrome, status notes, dates, long prose and the page
        title never qualify; an isolated line stays CONTEXT.
        """
        for index, row in enumerate(rows):
            text = row['shown'].strip()
            if index == 0:
                # The first line is the page title, never one of its questions.
                continue
            if row['kind'] != 'CONTEXT' or (row['heading'] and not row['listed']) \
                    or not 4 <= len(text) <= LISTED_MAX:
                continue
            if text.endswith(('。', ':', '：')) or '。' in text or SECTION.search(text) or CHROME.search(text) \
                    or STATUS.search(text) or NARRATION.search(text):
                continue
            if not re.search(r'[A-Za-z\u4e00-\u9fff]', text):
                continue
            if row['section']:
                # A round section ("一面") is a list by convention; its short
                # non-narrative items are the questions that were asked.
                row['kind'] = 'QUESTION'
                continue
            for other in rows[max(0, index - NEIGHBOUR):index + NEIGHBOUR + 1]:
                if other is row or other['section'] != row['section'] or other['kind'] != 'QUESTION':
                    continue
                row['kind'] = 'QUESTION'
                break

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
        # An answer paragraph is the interviewer's material, never the candidate's
        # reported question; it must not be recycled as one.
        if actual not in ('QUESTION', 'REPORTED_TOPIC') or (actual == 'REPORTED_TOPIC' and kind != 'REPORTED_TOPIC'):
            raise EvidenceValidationError(
                f'Fragment is not an exact reported question: fragment_id={key}; source_kind={actual}; '
                f'text={self.display[key][:80]!r}')
        return span
