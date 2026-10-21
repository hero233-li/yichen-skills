#!/usr/bin/env python3
"""Pick Openverse sound effects for compiled cues and add them to a headless plan as local WAV audio.

Network access is limited to api.openverse.org (search) and cdn.freesound.org (preview MP3).
Each run writes a new work directory with the raw search responses, downloads, leveled WAV files,
report.json and credits.md, the license notice to relay when the draft is delivered.
"""
import argparse
from array import array
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import wave

import edit_plan

API = 'https://api.openverse.org/v1/audio/'
API_HOSTS = {'api.openverse.org'}
DOWNLOAD_HOSTS = {'cdn.freesound.org'}
USER_AGENT = 'yichen-jianying-edit-openverse-sfx/1 (local video editing, low volume)'
HEADLESS_SCHEMA = 'jy14-headless-plan/v1'
TRACK_PREFIX = 'Openverse 音效'
LICENSE_NAMES = {'cc0': 'CC0', 'by': 'CC BY', 'by-nc': 'CC BY-NC'}
COMMERCIAL_LICENSES = ('cc0', 'by')
DEFAULT_LICENSES = ('cc0', 'by', 'by-nc')
RATE = 48000
TARGET_RMS_DB = -20.0  # loudest 50 ms window after leveling; the cue volume scales from here
PEAK_CEILING_DB = -1.0
MAX_BOOST_DB = 24.0
ONSET_DB = -45.0
TAIL_DB = -60.0
MAX_API_BYTES = 2 * 1024 * 1024
MAX_AUDIO_BYTES = 8 * 1024 * 1024
ATTEMPTS_PER_SOUND = 4
ID_PATTERN = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
STOPWORDS = {'a', 'an', 'and', 'the', 'of', 'sound', 'sounds', 'effect', 'effects', 'sfx', 'fx'}
# Words that usually mean music, speech or several sounds in one file rather than one short effect.
MULTI_WORDS = {'loop', 'loops', 'music', 'musical', 'song', 'melody', 'ambience', 'ambient', 'atmosphere',
               'atmos', 'background', 'voice', 'vocal', 'speech', 'spoken', 'talking', 'pack', 'compilation',
               'collection', 'various', 'variations', 'multiple', 'set'}


def allowed_url(url, hosts):
    parts = urllib.parse.urlsplit(str(url))
    if parts.scheme != 'https' or parts.hostname not in hosts or parts.username or parts.password or \
            parts.port not in (None, 443):
        raise ValueError('Refusing a URL outside the Openverse/Freesound allowlist: %s' % (parts.hostname or '?'))
    return str(url)


class GuardedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, hosts):
        super().__init__()
        self.hosts = hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        allowed_url(newurl, self.hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def retry_after(headers):
    try:
        return max(1, int(headers.get('Retry-After', '10')))
    except (TypeError, ValueError):
        return 10


class Client:
    """Rate-aware HTTPS client; tests pass a fake opener and sleep."""

    def __init__(self, opener=None, sleep=time.sleep):
        self.opener = opener
        self.sleep = sleep
        self.searches = 0
        self.downloads = 0
        self.rate = {}

    def get(self, url, hosts, limit, accept):
        allowed_url(url, hosts)
        host = urllib.parse.urlsplit(url).hostname
        opener = self.opener or urllib.request.build_opener(GuardedRedirect(hosts))
        request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': accept})
        for attempt in range(3):
            try:
                with opener.open(request, timeout=30) as response:
                    allowed_url(response.geturl(), hosts)
                    data = response.read(limit + 1)
                    headers = response.headers
            except urllib.error.HTTPError as error:
                wait = retry_after(error.headers) if error.code == 429 else 2 * (attempt + 1) if error.code >= 500 else None
                if wait is None or wait > 60 or attempt == 2:
                    if error.code == 429:
                        raise RuntimeError('Openverse rate limit reached (anonymous 20/min, 200/day); '
                                           'retry later with a new --work directory.')
                    raise RuntimeError('HTTP %d from %s' % (error.code, host))
                self.sleep(wait)
                continue
            except (urllib.error.URLError, socket.timeout, ConnectionError, TimeoutError) as error:
                if attempt == 2:
                    raise RuntimeError('Network error from %s: %s' % (host, error))
                self.sleep(2 * (attempt + 1))
                continue
            if len(data) > limit:
                raise ValueError('Response from %s exceeds %d bytes' % (host, limit))
            return data, headers
        raise RuntimeError('Request to %s did not complete' % host)


def create(path, data):
    with Path(path).open('xb') as stream:
        stream.write(data)


def slug(query):
    name = re.sub(r'[^a-z0-9]+', '-', query).strip('-')[:40] or 'sound'
    return '%s-%s' % (name, hashlib.sha256(query.encode()).hexdigest()[:8])


def clean(value, limit=200):
    text = ' '.join(''.join(c if c.isprintable() else ' ' for c in str(value or '')).split())
    return text[:limit]


def link(value):
    text = str(value or '')
    parts = urllib.parse.urlsplit(text)
    return text if parts.scheme in ('http', 'https') and parts.hostname and not re.search(r'[\s<>|]', text) else None


def search(client, query, licenses, work=None):
    params = urllib.parse.urlencode({'q': query, 'source': 'freesound', 'license': ','.join(licenses),
                                     'length': 'shortest', 'page_size': 20, 'mature': 'false'})
    data, headers = client.get(API + '?' + params, API_HOSTS, MAX_API_BYTES, 'application/json')
    client.searches += 1
    client.rate = {k: headers.get('x-ratelimit-available-' + k) for k in ('anon_burst', 'anon_sustained')}
    if work is not None:
        create(work / 'search' / (slug(query) + '.json'), data)
    results = json.loads(data).get('results')
    if not isinstance(results, list):
        raise ValueError('Unexpected Openverse response for "%s"' % query)
    if str(client.rate.get('anon_burst')) in {'0', '1'}:
        client.sleep(61)  # stay under the anonymous per-minute window
    return results


def words(text):
    return re.findall(r'[a-z0-9]+', str(text).lower())


def evaluate(result, terms, max_us, licenses, position):
    """Return a sort key and its evidence, or None and the rejection reason."""
    license = result.get('license')
    if license not in licenses:
        return None, 'license'
    if result.get('mature') or result.get('unstable__sensitivity'):
        return None, 'sensitive'
    if not ID_PATTERN.fullmatch(str(result.get('id', ''))):
        return None, 'id'
    try:
        allowed_url(result.get('url', ''), DOWNLOAD_HOSTS)
    except ValueError:
        return None, 'host'
    duration = result.get('duration')
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or \
            not 50 <= duration <= max(10000, 6 * max_us / 1000):
        return None, 'duration'
    text = set(words(result.get('title', '')))
    for tag in result.get('tags') or []:
        text.update(words(tag.get('name', '') if isinstance(tag, dict) else tag))
    matched = sum(any(w == t or (len(t) >= 3 and w.startswith(t)) for w in text) for t in terms)
    negatives = sorted((text & MULTI_WORDS) - set(terms))
    full = matched == len(terms)
    half = matched > 0 and 2 * matched >= len(terms)
    if negatives:
        tier = 2 if full else None
    else:
        tier = 0 if full else 1 if half else None
    if tier is None:
        return None, 'no-match'  # title and tags do not name the requested sound
    excess = max(0.0, duration / 1000 - 1.5 * max_us / 1e6) / (max_us / 1e6)
    # Clean matches before music/loop-like ones; within each, CC BY-NC only when no CC0/CC BY fits.
    # A file over twice the slot ranks below an attribution-only license; otherwise CC0 comes first.
    key = (tier == 2, license == 'by-nc', tier, excess > 0.5, license == 'by', round(excess, 3), position)
    return key, {'tier': tier, 'matched_terms': matched, 'terms': len(terms), 'negative_words': negatives,
                 'duration_excess': round(excess, 3), 'api_rank': position}


def rank(results, query, max_us, licenses):
    terms = [w for w in words(query) if w not in STOPWORDS]
    ranked, rejected = [], {}
    for position, result in enumerate(results):
        if not isinstance(result, dict):
            continue
        key, detail = evaluate(result, terms, max_us, licenses, position)
        if key is None:
            rejected[detail] = rejected.get(detail, 0) + 1
        else:
            ranked.append((key, result, detail))
    ranked.sort(key=lambda item: item[0])
    return ranked, rejected


def download(client, result, work):
    path = work / 'downloads' / (result['id'] + '.mp3')
    if path.exists():
        return path
    data, headers = client.get(result['url'], DOWNLOAD_HOSTS, MAX_AUDIO_BYTES, 'audio/mpeg')
    client.downloads += 1
    kind = str(headers.get('Content-Type', '')).split(';')[0].strip().lower()
    mpeg = data[:3] == b'ID3' or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0)
    if not kind.startswith('audio/') or not mpeg:
        raise ValueError('Downloaded preview is not an MP3 file')
    create(path, data)
    return path


def decode(source, target):
    probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'a', '-show_streams',
                                                '-of', 'json', str(source)], timeout=60))
    streams = probe.get('streams', [])
    if len(streams) != 1:
        raise ValueError('Preview must contain exactly one audio stream')
    channels = 1 if streams[0].get('channels') == 1 else 2
    partial = target.with_name(target.stem + '.partial.wav')
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-n', '-i', str(source), '-map', '0:a:0', '-vn', '-sn',
                    '-dn', '-ac', str(channels), '-ar', str(RATE), '-c:a', 'pcm_s16le', '-f', 'wav', str(partial)],
                   check=True, timeout=120, capture_output=True)
    os.replace(partial, target)


def dbfs(value):
    return 20 * math.log10(value / 32768) if value > 0 else -math.inf


def shape(decoded, target, max_us):
    """Trim to the onset, cap the length, level the loudest 50 ms and write a 48 kHz PCM WAV."""
    with wave.open(str(decoded), 'rb') as stream:
        channels, width, rate = stream.getnchannels(), stream.getsampwidth(), stream.getframerate()
        if width != 2 or rate != RATE or channels not in (1, 2):
            raise ValueError('Decoded preview has an unexpected PCM format')
        samples = array('h')
        samples.frombytes(stream.readframes(stream.getnframes()))
    if sys.byteorder == 'big':
        samples.byteswap()
    frames = len(samples) // channels
    if channels == 1:
        level = [abs(v) for v in samples]
    else:
        level = [max(abs(a), abs(b)) for a, b in zip(samples[0::2], samples[1::2])]
    onset = next((i for i, v in enumerate(level) if v >= 32768 * 10 ** (ONSET_DB / 20)), None)
    if onset is None:
        raise ValueError('Preview is silent')
    tail = 32768 * 10 ** (TAIL_DB / 20)
    last = next(i for i in range(frames - 1, -1, -1) if level[i] >= tail)
    start = max(0, onset - RATE * 3 // 1000)
    end = min(frames, last + RATE * 50 // 1000)
    limit = max_us * RATE // 1000000
    truncated = end - start > limit
    count = (min(end, start + limit) - start) // 48 * 48  # whole milliseconds keep probed durations exact
    if count < RATE * 50 // 1000:
        raise ValueError('Preview is shorter than 50 ms after trimming')
    window = RATE * 50 // 1000
    loudest = 0.0
    for first in range(start, max(start + 1, start + count - window + 1), window // 2):
        last_frame = min(start + count, first + window)
        if channels == 1:
            power = sum(v * v for v in samples[first:last_frame])
        else:
            pairs = samples[first * 2:last_frame * 2]
            power = sum((a + b) * (a + b) / 4 for a, b in zip(pairs[0::2], pairs[1::2]))
        loudest = max(loudest, math.sqrt(power / (last_frame - first)))
    peak, rms = dbfs(max(level[start:start + count])), dbfs(loudest)
    if rms < -55:
        raise ValueError('Preview is too quiet to level reliably')
    gain_db = min(TARGET_RMS_DB - rms, MAX_BOOST_DB, PEAK_CEILING_DB - peak)
    gain = 10 ** (gain_db / 20)
    fade_in = RATE * 2 // 1000 if start else 0
    fade_out = min(count // 2, RATE * 40 // 1000) if truncated else min(count // 4, RATE * 5 // 1000)
    out = array('h', bytes(count * channels * 2))
    for i in range(count):
        g = gain
        if i < fade_in:
            g *= i / fade_in
        if count - i <= fade_out:
            g *= (count - i - 1) / fade_out
        base = (start + i) * channels
        for c in range(channels):
            v = round(samples[base + c] * g)
            out[i * channels + c] = 32767 if v > 32767 else -32768 if v < -32768 else v
    if sys.byteorder == 'big':
        out.byteswap()
    with Path(target).open('xb') as raw, wave.open(raw, 'wb') as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(2)
        stream.setframerate(RATE)
        stream.writeframes(out.tobytes())
    return {'duration_us': count // 48 * 1000, 'channels': channels, 'onset_trim_ms': round(start * 1000 / RATE),
            'truncated': truncated, 'gain_db': round(gain_db, 2), 'peak_dbfs_before': round(peak, 2),
            'rms50ms_peak_dbfs_before': round(rms, 2)}


def probe_us(path):
    data = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                                               '-of', 'json', str(path)], timeout=60))
    audio = [s for s in data['streams'] if s.get('codec_type') == 'audio']
    if len(audio) != 1:
        raise ValueError('Leveled WAV must contain one audio stream')
    return round(float(audio[0].get('duration', data['format'].get('duration', 0))) * 1e6)


def record(result, detail, info, mp3, wav):
    license = result['license']
    version = clean(result.get('license_version'), 10)
    return {'id': result['id'], 'title': clean(result.get('title')) or result['id'],
            'creator': clean(result.get('creator')) or 'unknown', 'creator_url': link(result.get('creator_url')),
            'license': license, 'license_version': version,
            'license_name': ('%s %s' % (LICENSE_NAMES[license], version)).strip(),
            'license_url': link(result.get('license_url')), 'source_page': link(result.get('foreign_landing_url')),
            'openverse_page': 'https://openverse.org/audio/' + result['id'],
            'attribution': clean(result.get('attribution'), 600), 'source_duration_ms': result.get('duration'),
            'preview_mp3': str(mp3), 'preview_sha256': edit_plan.digest(mp3),
            'wav': str(wav), 'wav_sha256': edit_plan.digest(wav), 'processing': info, 'selection': detail,
            'commercial_use': license != 'by-nc', 'attribution_required': license != 'cc0'}


def resolve(client, query, max_us, ranked, work):
    failures = []
    for _, result, detail in ranked[:ATTEMPTS_PER_SOUND]:
        try:
            mp3 = download(client, result, work)
            decoded = work / 'decoded' / (result['id'] + '.wav')
            if not decoded.exists():
                decode(mp3, decoded)
            wav = work / 'sounds' / ('%s-%s-%dms.wav' % (slug(query), result['id'][:8], max_us // 1000))
            info = shape(decoded, wav, max_us)
            info['duration_us'] = min(info['duration_us'], probe_us(wav))
            return record(result, detail, info, mp3, wav), failures
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
            failures.append({'id': result.get('id'), 'title': clean(result.get('title')), 'error': clean(error, 300)})
    raise RuntimeError('no usable candidate among %d ranked results' % len(ranked))


def load_plan(path, compiled):
    plan = json.loads(Path(path).read_text())
    if plan.get('schema') != HEADLESS_SCHEMA or not isinstance(plan.get('tracks'), list) or not plan['tracks']:
        raise ValueError('Expected the jy14-headless-plan/v1 written by headless_draft.py from-compiled.')
    main = plan['tracks'][0]
    if main.get('type') != 'video' or not main.get('segments') or \
            any(segment.get('source') != compiled['source'] for segment in main['segments']):
        raise ValueError('The plan main video does not come from this compiled source.')
    if max(s['start_us'] + s['duration_us'] for s in main['segments']) != compiled['duration_us']:
        raise ValueError('The plan timeline length differs from the compiled plan.')
    if any(str(track.get('name', '')).startswith(TRACK_PREFIX) for track in plan['tracks']):
        raise ValueError('This plan already has Openverse sound tracks; start again from the from-compiled plan.')
    return plan


def place(plan, cues, sounds, total):
    tracks, placements = [], []
    for cue in sorted(cues, key=lambda c: c['start_us']):
        sound = sounds[cue['query'], cue['max_duration_us']]
        spec = {'source': sound['wav'], 'start_us': cue['start_us'],
                'duration_us': min(sound['processing']['duration_us'], total - cue['start_us']), 'volume': cue['volume']}
        track = next((t for t in tracks if t['segments'][-1]['start_us'] + t['segments'][-1]['duration_us']
                      <= spec['start_us']), None)
        if track is None:
            track = {'type': 'audio', 'name': '%s %d' % (TRACK_PREFIX, len(tracks) + 1), 'segments': []}
            tracks.append(track)
        track['segments'].append(spec)
        placements.append(dict(cue, sound_id=sound['id'], duration_us=spec['duration_us'], track=track['name']))
    plan['tracks'].extend(tracks)
    return placements


def clock(us):
    minutes, seconds = divmod(us / 1e6, 60)
    return '%02d:%04.1f' % (minutes, seconds)


def cell(text):
    return text.replace('|', '/').replace('`', "'")


def credits(used, placements):
    nc = [s for s in used.values() if s['license'] == 'by-nc']
    attributed = [s for s in used.values() if s['attribution_required']]
    lines = ['# 音效许可说明', '',
             '音效来自 Openverse 收录的 Freesound，由 openverse_sfx.py 自动挑选，并做了起音剪裁、截短和音量统一。'
             'Openverse 不核验许可状态，发布前可以打开下面的原始页面复核。', '', '## 使用限制', '']
    if nc:
        lines.append('- ⚠️ 用了 %d 个 CC BY-NC（禁止商用）音效：%s。它们只能用于非商业视频。商单、广告、带货、'
                     '给自己的付费产品引流，或开通了流量收益的视频都算商业用途；这类视频发布前请在剪映里替换它们，'
                     '或者重新运行 openverse_sfx.py 并加上 `--commercial`。'
                     % (len(nc), '、'.join('《%s》' % s['title'] for s in nc)))
    else:
        lines.append('- 没有 CC BY-NC 音效，本次音效都允许商用。')
    if attributed:
        lines.append('- 有 %d 个音效要求署名（CC BY / CC BY-NC）：把下面的署名文字放进视频简介或片尾。' % len(attributed))
    else:
        lines.append('- 全部是 CC0，不需要署名；下面的来源只用于追溯。')
    lines += ['', '## 署名文字（可直接粘贴）', '']
    if attributed:
        lines.append('音效：')
        for s in attributed:
            lines.append('- “%s” by %s，%s（%s），来源 Freesound：%s，已剪辑' % (
                s['title'], s['creator'], s['license_name'], s['license_url'] or '许可链接见原始页面',
                s['source_page'] or s['openverse_page']))
    else:
        lines.append('无需署名。')
    lines += ['', '## 本次用到的音效', '', '| 时间点 | 关键词 | 音效 | 作者 | 许可 | 原始页面 |',
              '|---|---|---|---|---|---|']
    for p in placements:
        s = used[p['sound_id']]
        lines.append('| %s | %s | %s | %s | %s | %s |' % (clock(p['start_us']), p['query'], cell(s['title']),
                                                       cell(s['creator']), s['license_name'],
                                                       s['source_page'] or s['openverse_page']))
    return '\n'.join(lines) + '\n'


def write_json(path, value):
    create(path, (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode())


def add(compiled_path, plan_path, out, work, commercial=False, client=None):
    compiled = json.loads(Path(compiled_path).read_text())
    edit_plan.validate_compiled(compiled)
    cues = compiled.get('openverse_sfx') or []
    if not cues:
        raise ValueError('The compiled plan has no Openverse sound cues; build the from-compiled plan directly.')
    plan = load_plan(plan_path, compiled)
    out, work = Path(out).resolve(), Path(work).resolve()
    if out.exists():
        raise ValueError('The output plan already exists; choose a new path.')
    work.mkdir(parents=True, mode=0o700, exist_ok=False)
    for name in ('search', 'downloads', 'decoded', 'sounds'):
        (work / name).mkdir(mode=0o700)
    licenses = COMMERCIAL_LICENSES if commercial else DEFAULT_LICENSES
    client = client or Client()
    report = {'schema': 'openverse-sfx-report/v1', 'created_at': datetime.now(timezone.utc).isoformat(),
              'compiled': str(Path(compiled_path).resolve()), 'compiled_sha256': edit_plan.digest(compiled_path),
              'input_plan': str(Path(plan_path).resolve()), 'license_policy': 'commercial' if commercial else 'default',
              'allowed_licenses': list(licenses), 'network_hosts': sorted(API_HOSTS | DOWNLOAD_HOSTS)}
    results = {query: search(client, query, licenses, work) for query in sorted({c['query'] for c in cues})}
    sounds, failures = {}, []
    for query, max_us in sorted({(c['query'], c['max_duration_us']) for c in cues}):
        ranked, rejected = rank(results[query], query, max_us, licenses)
        try:
            sound, attempts = resolve(client, query, max_us, ranked, work)
        except RuntimeError as error:
            failures.append({'query': query, 'max_duration_us': max_us, 'error': str(error), 'rejected': rejected})
            continue
        sound['failed_attempts'] = attempts
        sound['rejected_counts'] = rejected
        sound['alternatives'] = [{'id': r['id'], 'title': clean(r.get('title')), 'license': r['license'],
                                  'tier': d['tier']} for _, r, d in ranked if r['id'] != sound['id']][:3]
        sounds[query, max_us] = sound
    report.update(searches=client.searches, downloads=client.downloads, rate_limit_available=client.rate)
    if failures:
        report.update(status='failed', failures=failures, sounds=list(sounds.values()))
        write_json(work / 'report.json', report)
        names = ', '.join('"%s"' % f['query'] for f in failures)
        raise RuntimeError('No usable Openverse sound for %s under %s licenses; try other English keywords. '
                           'See %s' % (names, '/'.join(LICENSE_NAMES[x] for x in licenses), work / 'report.json'))
    placements = place(plan, cues, sounds, compiled['duration_us'])
    used = {}
    for sound in sounds.values():
        used.setdefault(sound['id'], sound)
    create(out, (json.dumps(plan, ensure_ascii=False, indent=2) + '\n').encode())
    create(work / 'credits.md', credits(used, placements).encode())
    licenses_used = {name: sum(s['license'] == name for s in used.values()) for name in licenses}
    summary = {'status': 'added', 'plan': str(out), 'cues': len(placements), 'sounds': len(used),
               'tracks': len({p['track'] for p in placements}), 'licenses': licenses_used,
               'noncommercial_used': any(s['license'] == 'by-nc' for s in used.values()),
               'attribution_required': any(s['attribution_required'] for s in used.values()),
               'credits': str(work / 'credits.md'), 'report': str(work / 'report.json'),
               'searches': client.searches, 'downloads': client.downloads, 'live_written': False}
    report.update(status='added', output_plan=str(out), placements=placements, sounds=list(used.values()),
                  summary=summary)
    write_json(work / 'report.json', report)
    return summary


def preview(query, max_duration, commercial=False, client=None):
    query = edit_plan.openverse_query(query)
    max_us = round(edit_plan.number(max_duration, 'max duration') * 1e6)
    if not edit_plan.OPENVERSE_MIN_US <= max_us <= edit_plan.OPENVERSE_MAX_US:
        raise ValueError('max duration must be 0.1-8 seconds')
    licenses = COMMERCIAL_LICENSES if commercial else DEFAULT_LICENSES
    client = client or Client()
    ranked, rejected = rank(search(client, query, licenses), query, max_us, licenses)
    return {'query': query, 'allowed_licenses': list(licenses), 'rejected_counts': rejected,
            'rate_limit_available': client.rate,
            'candidates': [{'id': r['id'], 'title': clean(r.get('title')), 'creator': clean(r.get('creator')),
                            'license': r['license'], 'duration_ms': r.get('duration'),
                            'source_page': link(r.get('foreign_landing_url')), **detail}
                           for _, r, detail in ranked[:8]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('add', help='download, level and place every Openverse cue of a compiled plan')
    p.add_argument('--compiled', required=True, type=Path)
    p.add_argument('--plan', required=True, type=Path, help='plan written by headless_draft.py from-compiled')
    p.add_argument('--out', required=True, type=Path, help='new plan path for build/create')
    p.add_argument('--work', required=True, type=Path, help='new directory for downloads, report and credits')
    p.add_argument('--commercial', action='store_true', help='only CC0 and CC BY; excludes CC BY-NC')
    p = sub.add_parser('search', help='rank candidates for one query without downloading')
    p.add_argument('--query', required=True)
    p.add_argument('--max-duration', type=float, default=1.5)
    p.add_argument('--commercial', action='store_true')
    args = parser.parse_args()
    if args.command == 'add':
        result = add(args.compiled, args.plan, args.out, args.work, args.commercial)
    else:
        result = preview(args.query, args.max_duration, args.commercial)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(type(error).__name__ + ': ' + str(error), file=sys.stderr)
        raise SystemExit(2)
