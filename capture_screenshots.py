import asyncio
import base64
import json
import os
import sys
import time
import urllib.request
import websockets

if sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

SCREENSHOT_DIR = r"D:\railgadi\screenshots"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)
PORT = 9222


async def send_cdp(ws, method, params=None, msg_id=[1]):
    curr_id = msg_id[0]
    msg_id[0] += 1
    req = {"id": curr_id, "method": method, "params": params or {}}
    await ws.send(json.dumps(req))
    while True:
        resp = await ws.recv()
        data = json.loads(resp)
        if data.get("id") == curr_id:
            return data.get("result", {})


async def eval_js(ws, expr):
    res = await send_cdp(ws, "Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
    return res.get("result", {}).get("value")


async def capture_screenshot(ws, filename):
    res = await send_cdp(ws, "Page.captureScreenshot", {"format": "png"})
    data = base64.b64decode(res["data"])
    out_path = os.path.join(SCREENSHOT_DIR, filename)
    with open(out_path, "wb") as f:
        f.write(data)
    print(f"Captured: {filename} ({len(data)} bytes)")


async def main():
    print("Connecting to Chrome DevTools...")
    ws_url = None
    for _ in range(30):
        try:
            with urllib.request.urlopen(f"http://localhost:{PORT}/json", timeout=2) as resp:
                targets = json.loads(resp.read().decode())
                for t in targets:
                    if t.get("type") == "page" and "localhost:8501" in t.get("url", ""):
                        ws_url = t.get("webSocketDebuggerUrl")
                        break
                if ws_url:
                    break
        except Exception:
            pass
        await asyncio.sleep(1)

    if not ws_url:
        print("Could not find Streamlit target.")
        return

    async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
        await send_cdp(ws, "Page.enable")
        await send_cdp(ws, "Runtime.enable")

        # Set desktop viewport
        await send_cdp(ws, "Emulation.setDeviceMetricsOverride", {
            "width": 1440,
            "height": 960,
            "deviceScaleFactor": 1,
            "mobile": False,
        })

        # Wait until stTab elements exist and spinners disappear
        print("Waiting for [data-testid='stTab'] elements and completion of spinners...")
        for i in range(40):
            tabs_count = await eval_js(ws, "document.querySelectorAll('[data-testid=\"stTab\"]').length")
            spinners = await eval_js(ws, "document.querySelectorAll('[data-testid=\"stSpinner\"]').length")
            print(f"Check {i+1}: tabs={tabs_count}, spinners={spinners}")
            if tabs_count and tabs_count >= 5 and spinners == 0:
                print("Found all 5 tabs and spinners finished!")
                break
            await asyncio.sleep(2)

        tabs = [
            ("01_live_status_tab.png", 0, "Live Status"),
            ("02_route_map_tab.png", 1, "Route Map"),
            ("03_elevation_profile_tab.png", 2, "Elevation Profile"),
            ("04_route_weather_tab.png", 3, "Route Weather"),
            ("05_terrain_points_tab.png", 4, "Terrain Points"),
        ]

        for filename, tab_idx, name in tabs:
            print(f"Activating Tab {tab_idx}: {name}...")
            clicked = await eval_js(ws, f"""
                (function() {{
                    const tabs = document.querySelectorAll('[data-testid="stTab"]');
                    if (tabs.length > {tab_idx}) {{
                        tabs[{tab_idx}].click();
                        return tabs[{tab_idx}].innerText.trim();
                    }}
                    return null;
                }})()
            """)
            print(f"Clicked tab {tab_idx}: {clicked}")
            # Wait for content to render (maps, line charts, weather tables)
            if tab_idx == 4:
                print("Waiting for Overpass query to complete on Tab 4...")
                for _ in range(25):
                    spin = await eval_js(ws, "document.querySelectorAll('[data-testid=\"stSpinner\"]').length")
                    if spin == 0:
                        break
                    await asyncio.sleep(1)
            await asyncio.sleep(5)
            await eval_js(ws, "window.scrollTo(0, 0)")
            await capture_screenshot(ws, filename)

        # Mobile View (Route Map on narrow width)
        print("Capturing narrow mobile view (390x844)...")
        await send_cdp(ws, "Emulation.setDeviceMetricsOverride", {
            "width": 390,
            "height": 844,
            "deviceScaleFactor": 2,
            "mobile": True,
        })
        await eval_js(ws, "document.querySelectorAll('[data-testid=\"stTab\"]')[1].click()")
        await asyncio.sleep(4)
        await eval_js(ws, "window.scrollTo(0, 0)")
        await capture_screenshot(ws, "06_mobile_view.png")

        print("All screenshots successfully captured!")


if __name__ == "__main__":
    asyncio.run(main())
