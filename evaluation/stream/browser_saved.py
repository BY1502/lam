"""Open saved real-model results on final UI; block every POST (zero generation)."""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

OUT = Path('/home/yt/lam/evaluation/results/chatbot-stream-20261102-01')


async def main():
    row = json.loads((OUT / 'browser_real_runs.jsonl').read_text())
    attempted_posts = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        page = await browser.new_page(viewport={'width': 1350, 'height': 1050})
        await page.add_init_script('sessionStorage.setItem("lam-chat-session-v1", ' + json.dumps(row['session_id']) + ');')

        async def guard(route):
            if route.request.method == 'POST':
                attempted_posts.append(route.request.url)
                await route.abort()
            else:
                await route.continue_()

        await page.route('**/api/**', guard)
        await page.goto('http://127.0.0.1:8080')
        await page.get_by_role('tab', name='챗봇', exact=True).click()
        await page.wait_for_function('chatState.session && !chatState.loading')
        assert await page.locator('.chat-reply').last.inner_text() == row['record']['reply']
        await page.locator('.assistant details').first.evaluate('(e)=>e.open=true')
        await page.screenshot(path=str(OUT / 'ui_final_saved.png'), full_page=True)
        assert not attempted_posts, attempted_posts
        (OUT / 'ui_final_readonly_checks.json').write_text(json.dumps({
            'saved_reply_exact': True, 'generation_calls_added': 0,
            'all_posts_blocked': True, 'attempted_posts': attempted_posts,
            'method': '완료한 실제 vLLM 결과를 최종 UI에서 GET으로만 열람. 생성 POST는 차단.'
        }, ensure_ascii=False, indent=2))
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
