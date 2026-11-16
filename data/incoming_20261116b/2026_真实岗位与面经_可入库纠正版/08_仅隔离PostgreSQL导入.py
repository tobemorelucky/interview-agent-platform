# coding: utf-8
"""将数据集只写入现有 *_sandbox_* 库的 sbx_* 夹具表。只允许手工明确指定的隔离库。"""
from pathlib import Path
import os,json,hashlib,sys
try: import psycopg
except ImportError: sys.exit('需要先在隔离环境安装 psycopg；否则使用07_一键导入SQLite.py')
root=Path(__file__).resolve().parent
conninfo=os.getenv('SANDBOX_DATABASE_URL','')
if not conninfo: sys.exit('必须明确设置 SANDBOX_DATABASE_URL；绝不使用 DATABASE_URL 回退')
files={
 'sbx_jds_sourced':('01_真实岗位来源_完整结构化改写_非逐字.jsonl','jd_id'),
 'sbx_jds_synthetic':('02_50份完整合成JD_可直接测试入库.jsonl','jd_id'),
 'sbx_interview_flows':('03_三份2026公开面经_完整流程元数据.jsonl','flow_id'),
 'sbx_interview_questions':('04_三份2026面经_按顺序改写问题.jsonl','question_id'),
 'sbx_answer_pairs':('05_20题40份模拟正确错误回答.jsonl','answer_id')}
with psycopg.connect(conninfo, autocommit=False) as conn:
    with conn.cursor() as cur:
        cur.execute('SELECT current_database(), current_user')
        db,user=cur.fetchone()
        if '_sandbox_' not in db or db in ('interview_agent','postgres','template1'):
            raise RuntimeError('拒绝写入：数据库名不是明确隔离的 *_sandbox_*: '+str(db))
        if os.getenv('SANDBOX_CONFIRM_DATABASE','')!=db:
            raise RuntimeError('SANDBOX_CONFIRM_DATABASE 必须准确等于 '+db)
        cur.execute('SELECT current_setting(\'default_transaction_read_only\')')
        for table,(fn,key) in files.items():
            cur.execute(f'CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, provenance TEXT NOT NULL, payload_json JSONB NOT NULL, content_sha256 TEXT NOT NULL)')
            rows=[json.loads(line) for line in (root/fn).read_text(encoding='utf-8').splitlines() if line.strip()]
            for r in rows:
                raw=json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(',',':'))
                prov=r.get('provenance') or r.get('source_type') or r.get('question_provenance') or 'SOURCE_METADATA'
                cur.execute(f'INSERT INTO {table}(id,provenance,payload_json,content_sha256) VALUES (%s,%s,%s::jsonb,%s) ON CONFLICT (id) DO UPDATE SET provenance=EXCLUDED.provenance,payload_json=EXCLUDED.payload_json,content_sha256=EXCLUDED.content_sha256', (r[key],prov,raw,hashlib.sha256(raw.encode()).hexdigest()))
            cur.execute(f'SELECT COUNT(*) FROM {table}')
            assert cur.fetchone()[0]==len(rows)
            print(table,len(rows))
    conn.commit()
print('OK: 写入现有隔离库 sbx_* 夹具表；没有改动正式业务表。')
