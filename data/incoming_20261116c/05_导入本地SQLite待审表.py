#!/usr/bin/env python3
"""Imports verifiable source-index and paraphrased interviews to a new disposable SQLite STAGING database only."""
import json, sqlite3, argparse
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--out',default='review_staging.sqlite3');a=p.parse_args()
out=Path(a.out)
if out.exists():raise SystemExit(f'Refusing to overwrite existing DB: {out}')
with sqlite3.connect(out) as db:
 db.executescript('CREATE TABLE source_jobs (id TEXT PRIMARY KEY, company TEXT, title TEXT, source_url TEXT, original_jd_text TEXT, verified INTEGER, payload_json TEXT); CREATE TABLE interview_flows (id TEXT PRIMARY KEY, source_url TEXT, payload_json TEXT); CREATE TABLE questions (id TEXT PRIMARY KEY, flow_id TEXT, question TEXT, payload_json TEXT);')
 for line in Path('01_官方岗位原始来源种子_31条.jsonl').read_text(encoding='utf8').splitlines():
  o=json.loads(line);db.execute('INSERT INTO source_jobs VALUES (?,?,?,?,?,?,?)',(o['id'],o['company'],o['title'],o['source_url'],None,0,json.dumps(o,ensure_ascii=False)))
 for line in Path('02_公开面经流程_6场.jsonl').read_text(encoding='utf8').splitlines():
  o=json.loads(line);db.execute('INSERT INTO interview_flows VALUES (?,?,?)',(o['flow_id'],o['report_url'],json.dumps(o,ensure_ascii=False)))
 for line in Path('03_面经有出处改写题目.jsonl').read_text(encoding='utf8').splitlines():
  o=json.loads(line);db.execute('INSERT INTO questions VALUES (?,?,?,?)',(o['id'],o['flow_id'],o['question_paraphrase'],json.dumps(o,ensure_ascii=False)))
 for tab in ('source_jobs','interview_flows','questions'):
  print(tab,db.execute('SELECT COUNT(*) FROM '+tab).fetchone()[0])
print('DONE staging only; source_jobs.verified=0; NEVER use as evidence-verified or business JD')
