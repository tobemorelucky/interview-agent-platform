# coding: utf-8
"""只创建数据集SQLite，绝不连接或修改用户的生产PostgreSQL。"""
from pathlib import Path
import sqlite3, json, hashlib
ROOT=Path(__file__).resolve().parent
DEST=ROOT/'local_sandbox_dataset.sqlite3'
DATASETS={
 'sbx_jds_sourced':'01_真实岗位来源_完整结构化改写_非逐字.jsonl',
 'sbx_jds_synthetic':'02_50份完整合成JD_可直接测试入库.jsonl',
 'sbx_interview_flows':'03_三份2026公开面经_完整流程元数据.jsonl',
 'sbx_interview_questions':'04_三份2026面经_按顺序改写问题.jsonl',
 'sbx_answer_pairs':'05_20题40份模拟正确错误回答.jsonl',
}
with sqlite3.connect(DEST) as conn:
    for table,fn in DATASETS.items():
        conn.execute(f'CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, provenance TEXT NOT NULL, payload_json TEXT NOT NULL, content_sha256 TEXT NOT NULL)')
        p=ROOT/fn
        rows=[json.loads(line) for line in p.read_text(encoding='utf-8').splitlines() if line.strip()]
        key={'sbx_jds_sourced':'jd_id','sbx_jds_synthetic':'jd_id','sbx_interview_flows':'flow_id','sbx_interview_questions':'question_id','sbx_answer_pairs':'answer_id'}[table]
        for r in rows:
            raw=json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(',',':'))
            prov=r.get('provenance') or r.get('source_type') or r.get('question_provenance') or 'SOURCE_METADATA'
            conn.execute(f'INSERT INTO {table}(id,provenance,payload_json,content_sha256) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET provenance=excluded.provenance,payload_json=excluded.payload_json,content_sha256=excluded.content_sha256', (r[key],prov,raw,hashlib.sha256(raw.encode()).hexdigest()))
        count=conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
        assert count==len(rows),(table,count,len(rows))
        print(f'{table}: {count} rows')
print('DONE', DEST)
