"""Record the newly built local product, not a transferred video."""
import asyncio,json,shutil,os,subprocess
from pathlib import Path
from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'artifacts';URL='http://127.0.0.1:8087'
async def main():
    OUT.mkdir(exist_ok=True)
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_PATH') or None,headless=True)
        context=await browser.new_context(viewport={'width':1440,'height':950},record_video_dir=str(OUT/'private'),record_video_size={'width':1440,'height':950})
        page=await context.new_page();await page.goto(URL);await page.wait_for_selector('#examples button');await page.screenshot(path=str(OUT/'start-desktop.png'),full_page=True)
        await page.wait_for_timeout(3500)
        await page.locator('#question').fill('Хочу взять отпуск. Как его оформить?')
        await page.locator('#ask').click();await page.locator('.exchange').first.wait_for(timeout=90000)
        assert (await page.locator('.exchange').first.locator('.answer-label').text_content()).strip()=='По документации'
        assert '/kb/vacation' in (await page.locator('.exchange').first.locator('.source-link').first.get_attribute('href'))
        await page.locator('.sources details').first.locator('summary').click();await page.screenshot(path=str(OUT/'answer-desktop.png'),full_page=True)
        await page.wait_for_timeout(10000)
        await page.locator('#question').fill('Есть ли денежные штрафы за опоздание?');await page.locator('#ask').click();await page.locator('.exchange').nth(1).wait_for(timeout=90000)
        assert (await page.locator('.exchange').nth(1).locator('.answer-label').text_content()).strip()=='По документации'
        await page.wait_for_timeout(9000)
        await page.reload();await page.locator('.exchange').nth(1).wait_for(timeout=10000)
        await page.locator('#prepare').click();await page.locator('#support-dialog').wait_for(state='visible');await page.wait_for_timeout(8000)
        await page.locator('#support-dialog [data-close]').click();await page.locator('#show-coverage').click();await page.wait_for_timeout(9000)
        await page.locator('#coverage-dialog [data-close]').click();await page.locator('#new-conversation').click();await page.locator('.exchange').first.wait_for(state='detached');await page.goto(URL+'/about');await page.wait_for_timeout(13000)
        await page.goto(URL+'/kb/vacation');await page.wait_for_timeout(6000)
        video=page.video;await context.close();path=Path(await video.path());shutil.copyfile(path,OUT/'demo.webm')
        subprocess.run(['ffmpeg','-y','-hide_banner','-loglevel','error','-i',str(OUT/'demo.webm'),
                        '-c:v','libx264','-preset','veryfast','-crf','28','-pix_fmt','yuv420p',
                        '-movflags','+faststart','-an',str(OUT/'demo.mp4')],check=True)
        mobile=await browser.new_context(viewport={'width':390,'height':844},device_scale_factor=1)
        mp=await mobile.new_page();await mp.goto(URL);await mp.wait_for_selector('#examples button');await mp.screenshot(path=str(OUT/'start-mobile.png'),full_page=True)
        report={'desktop_title':await mp.title(),'answer_language':'ru','reload_preserved_two_exchanges':True,'new_conversation_cleared_history':True,'mobile_scroll_width':await mp.evaluate('document.documentElement.scrollWidth'),
                'mobile_inner_width':390,'source_url':URL+'/kb/vacation','about_url':URL+'/about',
                'video_bytes':(OUT/'demo.webm').stat().st_size,'mp4_bytes':(OUT/'demo.mp4').stat().st_size}
        (OUT/'browser-check.json').write_text(json.dumps(report,indent=2)+'\n')
        await mobile.close();await browser.close()
asyncio.run(main())
