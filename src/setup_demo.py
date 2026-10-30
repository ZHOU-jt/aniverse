"""Create a local teaching demo without importing any real user records."""
import json
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEMO_USERNAME = 'DemoUser'
DEMO_EMAIL = 'demo@example.invalid'
DEMO_PASSWORD = 'DemoOnly!2026'


def initialize_demo(module):
    """Only initialize an empty database. Never reset existing application data."""
    records = json.loads((ROOT / 'data/anime_seed.json').read_text(encoding='utf-8'))
    db = module.get_db()
    try:
        db.execute('BEGIN IMMEDIATE')
        tables = ['anime', 'users', 'reviews', 'forum_posts', 'forum_replies', 'email_codes', 'email_send_locks']
        if any(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in tables):
            raise RuntimeError('Database is not empty; initialization was cancelled. No existing data was overwritten.')
        columns = [row[1] for row in db.execute('PRAGMA table_info(anime)')]
        assert all(set(record).issubset(columns) for record in records)
        sql = 'INSERT INTO anime (' + ','.join(columns) + ') VALUES (' + ','.join('?' for _ in columns) + ')'
        db.executemany(sql, [[record.get(column) for column in columns] for record in records])
        from werkzeug.security import generate_password_hash
        uid = db.execute('INSERT INTO users(username,email,password,created_at) VALUES (?,?,?,CURRENT_TIMESTAMP)',
                         (DEMO_USERNAME, DEMO_EMAIL, generate_password_hash(DEMO_PASSWORD))).lastrowid
        anime_id = db.execute('SELECT id FROM anime WHERE mal_id = 16498').fetchone()[0]
        db.execute('INSERT INTO reviews(user_id,anime_id,rating,content) VALUES (?,?,?,?)',
                   (uid, anime_id, 9, '【演示评价】节奏与音乐给人留下了很深的印象。这里可以记录自己的观看体验。'))
        pid = db.execute('INSERT INTO forum_posts(user_id,title,content) VALUES (?,?,?)',
                         (uid, '本周片单交流（演示帖子）', '这是独立生成的课程演示数据，不含线上用户内容。\n你最近在看哪一部动画？欢迎分享推荐和观看感受。')).lastrowid
        db.execute('INSERT INTO forum_replies(post_id,user_id,content) VALUES (?,?,?)',
                   (pid, uid, '【演示回复】帖子和回复的存储时间采用 UTC，页面统一显示北京时间。'))
        db.commit()
        return {'anime': len(records), 'users': 1, 'reviews': 1, 'forum_posts': 1, 'forum_replies': 1}
    finally:
        db.close()


def main():
    env = ROOT / '.env'
    if not env.exists():
        # Each local installation gets a new random signing key, never the live key.
        env.write_text('ANIVERSE_SECRET_KEY=' + secrets.token_hex(32) + '\nFLASK_DEBUG=0\n', encoding='utf-8')
        try:
            env.chmod(0o600)
        except OSError:
            pass
    import app
    try:
        counts = initialize_demo(app)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print('Local demo initialized:', json.dumps(counts))
    print('Username:', DEMO_USERNAME)
    print('Password:', DEMO_PASSWORD)
    print('Start: python run.py')
    print('This known demo account is for local coursework only.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
