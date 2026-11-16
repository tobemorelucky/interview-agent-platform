#!/usr/bin/env python3
"""Only collects public official job page text to a LOCAL sandbox incoming directory.
No DB writes or LLM calls. Needs access to the source websites from the user's machine.
Use --mode browser if the page is JS rendered (pip install playwright; playwright install chromium).
"""
import argparse, hashlib, json, re, html, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone
from html.parser import HTMLParser
class TextExtractor(HTMLParser):
 def __init__(self):
  super().__init__(); self.parts=[]; self.skip=0
 def handle_starttag(self, tag, attrs):
  if tag in ('script','style','noscript'):self.skip+=1
  if tag in ('p','li','div','h1','h2','h3','br'): self.parts.append('\n')
 def handle_endtag(self,tag):
  if tag in ('script','style','noscript'): self.skip=max(0,self.skip-1)
 def handle_data(self,data):
  if not self.skip:self.parts.append(data)
def stdlib_fetch(url):
 req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 (research dataset; respect site policies)'})
 with urllib.request.urlopen(req,timeout=25) as r:
  if r.status!=200:raise ValueError(f'HTTP_{r.status}')
  raw=r.read(3_000_000);charset=r.headers.get_content_charset() or 'utf-8'
 return raw.decode(charset,errors='replace')
def browser_fetch(url):
 from playwright.sync_api import sync_playwright
 with sync_playwright() as p:
  browser=p.chromium.launch(headless=True)
  try:
   page=browser.new_page();page.goto(url,wait_until='domcontentloaded',timeout=45000)
   try:page.wait_for_timeout(1800)
   except Exception:pass
   return page.content(),page.inner_text('body')
  finally:browser.close()
def extract(page):
 ex=TextExtractor();ex.feed(page)
 lines=[re.sub(r'\s+',' ',x).strip() for x in ''.join(ex.parts).splitlines()]
 return '\n'.join(x for x in lines if x)
def run():
 ap=argparse.ArgumentParser();ap.add_argument('--source',default='01_官方岗位原始来源种子_31条.jsonl');ap.add_argument('--out',default='acquired_official_raw');ap.add_argument('--mode',choices=['stdlib','browser'],default='browser');a=ap.parse_args()
 inp=Path(a.source);out=Path(a.out);out.mkdir(exist_ok=True,parents=True)
 jobs=[json.loads(s) for s in inp.read_text(encoding='utf8').splitlines() if s.strip()]
 results=[]
 for j in jobs:
  status='FETCH_FAILED';reason='';body='';page=''
  try:
   if a.mode=='browser':page,body=browser_fetch(j['source_url'])
   else:page=stdlib_fetch(j['source_url']);body=extract(page)
   # Listing pages (e.g. Kylin) contain multiple jobs. Never attribute the whole page to one posting.
   if 'campusRecruitment/' in j['source_url']:
    status='LISTING_NEEDS_JOB_SECTION_EXTRACTION';reason='Original listing has many jobs; isolate exact title and corresponding requirements before validation'
   elif len(body)<200 or not any(k in body for k in ('职位要求','任职要求','岗位要求','职位描述','岗位职责')):
    status='BODY_MISSING_OR_JS_SHELL';reason='No usable role+requirement text'
   elif j['title'].split(' ')[-1].split('（')[0] not in body:
    status='ROLE_TITLE_NOT_VERIFIED';reason='Posted title not found; review title formatting manually'
   else:status='FETCHED_NEEDS_MANUAL_VERBATIM_VERIFICATION'
   if body:
    (out/(j['id']+'.txt')).write_text(body,encoding='utf8')
    (out/(j['id']+'.html')).write_text(page,encoding='utf8')
  except Exception as e:reason=str(e)[:260]
  results.append({'id':j['id'],'company':j['company'],'title':j['title'],'url':j['source_url'],'status':status,'reason':reason,'text_len':len(body),'text_sha256':hashlib.sha256(body.encode('utf8')).hexdigest() if body else None,'recorded_utc':datetime.now(timezone.utc).isoformat()})
  print(j['id'],status,len(body))
 (out/'fetch_manifest.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in results),encoding='utf8')
 print('READY_TO_REVIEW',sum(x['status']=='FETCHED_NEEDS_MANUAL_VERBATIM_VERIFICATION' for x in results),'TOTAL',len(results))
if __name__=='__main__':run()
