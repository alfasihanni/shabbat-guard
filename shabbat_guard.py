"""שומר שבת/חג לקמפיינים הממומנים של חני — גרסת ענן (GitHub Actions).

רץ כל 15 דקות. מחליט אם *כרגע* אנחנו בתוך חלון שבת/חג:
  - כיבוי: קבוע ב-14:00 ביום שלפני שבת/חג (היום של הדלקת הנרות)
  - הדלקה: בצאת הכוכבים (הבדלה) לפי Hebcal, תל אביב

מנגנון: כשנכנסים לחלון — שומר אילו קמפיינים פעילים (status=ACTIVE) לקובץ state.json
ומכבה אותם. במוצאי שבת/חג — מדליק בדיוק את אלה ששמר (לא מחייה קמפיינים ישנים).
ככה תופס אוטומטית כל קמפיין חדש שיהיה פעיל ב-14:00.

הטוקן מגיע ממשתנה סביבה META_ACCESS_TOKEN (GitHub Secret). שום סוד לא בקוד.
שעון קיץ/חורף מטופל אוטומטית — Hebcal מחשב לפי השקיעה האמיתית בכל תאריך.
"""

import datetime
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

STATE_PATH = Path(__file__).parent / 'state.json'

GEONAMEID = '293397'   # Tel Aviv, Israel
OFF_HOUR = 14          # כיבוי קבוע ב-14:00 ביום הדלקת הנרות
GRAPH = 'https://graph.facebook.com/v21.0'

TOKEN = os.environ['META_ACCESS_TOKEN']
ACCT = os.environ.get('META_AD_ACCOUNT_ID', 'act_904663747076281')


def log(msg):
    ts = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    print(f'{ts} — {msg}', flush=True)


def meta_get(path, params=None):
    pr = dict(params or {})
    pr['access_token'] = TOKEN
    url = f'{GRAPH}/{path}?' + urllib.parse.urlencode(pr)
    return json.loads(urllib.request.urlopen(url, timeout=30).read())


def meta_set_status(campaign_id, status):
    data = urllib.parse.urlencode({'status': status, 'access_token': TOKEN}).encode()
    req = urllib.request.Request(f'{GRAPH}/{campaign_id}', data=data, method='POST')
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def clean(s):
    return ''.join(c for c in (s or '') if c not in '‎‏‪‫‬‭‮').strip()


def fetch_intervals(now):
    """רשימת (off_start, on_time) — חלונות שבהם המודעות צריכות להיות כבויות.
    off_start = יום הדלקת הנרות בשעה 14:00 (שעון ישראל) ; on_time = זמן ההבדלה.
    """
    start = (now - datetime.timedelta(days=3)).date().isoformat()
    end = (now + datetime.timedelta(days=14)).date().isoformat()
    p = {
        'v': '1', 'cfg': 'json', 'maj': 'on', 'min': 'off', 'mod': 'off',
        'nx': 'off', 'i': 'on', 'c': 'on', 'geo': 'geoname',
        'geonameid': GEONAMEID, 'b': '18', 'start': start, 'end': end,
    }
    url = 'https://www.hebcal.com/hebcal?' + urllib.parse.urlencode(p)
    data = json.loads(urllib.request.urlopen(url, timeout=30).read())

    candles, havdalahs = [], []
    for it in data.get('items', []):
        cat = it.get('category')
        if cat == 'candles':
            candles.append(datetime.datetime.fromisoformat(it['date']))
        elif cat == 'havdalah':
            havdalahs.append(datetime.datetime.fromisoformat(it['date']))
    candles.sort()
    havdalahs.sort()

    intervals = []
    for c in candles:
        nxt = next((h for h in havdalahs if h > c), None)
        if not nxt:
            continue
        off_start = c.replace(hour=OFF_HOUR, minute=0, second=0, microsecond=0)
        intervals.append((off_start, nxt))
    intervals.sort()
    merged = []
    for s, e in intervals:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def should_be_off(now, intervals):
    for s, e in intervals:
        if s <= now < e:
            return True, e
    return False, None


def read_state():
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'phase': 'ON', 'paused_campaigns': []}


def write_state(state):
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    intervals = fetch_intervals(now)
    off_now, window_end = should_be_off(now, intervals)
    state = read_state()
    phase = state.get('phase', 'ON')

    if off_now:
        if phase != 'OFF':
            camps = meta_get(f'{ACCT}/campaigns',
                             {'fields': 'name,id,status', 'limit': 300})
            active = [c for c in camps.get('data', []) if c.get('status') == 'ACTIVE']
            ids = []
            for c in active:
                nm = clean(c.get('name', ''))
                try:
                    meta_set_status(c['id'], 'PAUSED')
                    ids.append({'id': c['id'], 'name': nm})
                    log(f'כיבוי לשבת/חג: {c["id"]} | {nm}')
                except Exception as ex:
                    log(f'שגיאה בכיבוי {c["id"]} ({nm}): {ex}')
            write_state({'phase': 'OFF', 'paused_campaigns': ids,
                         'window_end': window_end.isoformat() if window_end else None})
            log(f'נכנסנו לחלון שבת/חג — כובו {len(ids)} קמפיינים. הדלקה צפויה: {window_end}')
        else:
            log('בתוך חלון שבת/חג, כבר במצב OFF — אין שינוי')
    else:
        if phase == 'OFF':
            ids = state.get('paused_campaigns', [])
            for c in ids:
                try:
                    meta_set_status(c['id'], 'ACTIVE')
                    log(f'הדלקה במוצאי שבת/חג: {c["id"]} | {c.get("name","")}')
                except Exception as ex:
                    log(f'שגיאה בהדלקה {c["id"]}: {ex}')
            write_state({'phase': 'ON', 'paused_campaigns': []})
            log(f'יצאנו מחלון שבת/חג — הודלקו {len(ids)} קמפיינים')
        else:
            log('יום חול, מצב ON — אין שינוי')


if __name__ == '__main__':
    main()
