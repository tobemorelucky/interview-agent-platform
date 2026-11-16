#!/usr/bin/env python3
"""Discover more actual posting URLs from current official campus listings.
No fabricated jobs, no LLMs, no database writes, no login/paywall bypass.
Needs `pip install playwright && playwright install chromium` on the user's own computer.
"""
import json, re, argparse
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, urljoin
from playwright.sync_api import sync_playwright
LISTS = [
    ('沐瞳科技','https://moonton.jobs.feishu.cn/campus/position/list'),
    ('蔚来','https://nio.jobs.feishu.cn/campus/position/list'),
]
def normalize(url):
    p=urlsplit(url)
    return urlunsplit((p.scheme,p.netloc,p.path,'',''))
def run():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',default='discovered_postings.jsonl')
    parser.add_argument('--scrolls',type=int,default=20)
    args=parser.parse_args()
    found={}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            for company,url in LISTS:
                page=browser.new_page()
                try:
                    page.goto(url,wait_until='domcontentloaded',timeout=45000)
                    page.wait_for_timeout(1500)
                    previous=0
                    for i in range(args.scrolls):
                        for x in page.locator('a[href]').all():
                            href=x.get_attribute('href') or ''
                            absolute=urljoin(url,href)
                            u=normalize(absolute)
                            if '/position/' not in u or '/detail' not in u: continue
                            # Only same-company official careers site.
                            if urlsplit(u).netloc != urlsplit(url).netloc: continue
                            title=x.inner_text(timeout=500).strip().split('\n')[0] if x.is_visible() else ''
                            found[u]={'source_url':u,'company':company,'title_hint':title,'status':'DISCOVERED_URL_ONLY_NEEDS_FULLTEXT_AND_TITLE_CHECK'}
                        page.mouse.wheel(0,3000)
                        page.wait_for_timeout(550)
                        if i>5 and len(found)==previous:break
                        previous=len(found)
                    print(company,'discovered cumulative:',len(found))
                except Exception as e:
                    print(company,'LISTING_UNAVAILABLE:',str(e)[:200])
                finally:page.close()
        finally:browser.close()
    out=Path(args.out)
    if out.exists():raise SystemExit('Refusing to overwrite '+str(out))
    out.write_text(''.join(json.dumps(v,ensure_ascii=False)+'\n' for v in found.values()),encoding='utf8')
    print('Saved',len(found),'real discovered URLs; NOT verified fulltext JD records')
if __name__=='__main__':run()
