"""Isolated regression tests. No live database access and no real email sends."""
import base64
import io
import json
import os
import smtplib
import sqlite3
import ssl
import sys
import tempfile
import threading
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SANDBOX = tempfile.TemporaryDirectory(prefix='aniverse-tests-')
os.environ['ANIVERSE_DB_PATH'] = str(Path(SANDBOX.name) / 'test.db')
os.environ['ANIVERSE_UPLOAD_FOLDER'] = str(Path(SANDBOX.name) / 'uploads')
import app as module
import mailer
import setup_demo
import sync_anime
from werkzeug.security import generate_password_hash, check_password_hash


class ApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        module.app.config.update(TESTING=True, SECRET_KEY='isolated-coursework-tests')
        cls.password_hash = generate_password_hash('TestOnly!2026')

    def setUp(self):
        db = module.get_db()
        for table in ['forum_replies', 'forum_posts', 'reviews', 'users', 'email_codes', 'email_send_locks', 'anime']:
            db.execute('DELETE FROM ' + table)
        db.execute('INSERT INTO users(id,username,email,password) VALUES (1,?,?,?)', ('TestUser', 'existing@example.invalid', self.password_hash))
        season, year, _, _ = module.current_season_info()
        rows = [
            (1, 16498, 'Attack on Titan', '進撃の巨人', '进击的巨人', 'finished', 8.5, 2013, 2013, 4, 7, 'SPRING', 'Action', 1, 1000),
            (2, 20, 'Naruto', 'ナルト', '火影忍者', 'finished', 8.0, 2002, 2002, 10, 3, 'FALL', 'Adventure', 1, 900),
            (3, 900003, 'Current Show', '', '', 'airing', 7.0, year, year, 10, 1, season, 'Comedy', 0, 800),
            (4, 900004, 'Future Show', '', '', 'upcoming', None, year + 1, year + 1, 1, 1, 'WINTER', 'Drama', 0, 700),
        ]
        rows += [(n, 900000+n, f'Fixture {n}', '', '', 'finished', 6.5, 2020, 2020, 4, 1, 'SPRING', 'Comedy', 0, 1) for n in range(5, 30)]
        db.executemany('INSERT INTO anime(id,mal_id,title,title_japanese,title_zh,status,mal_score,year,start_year,start_month,start_day,season,genres,is_classic,popularity) VALUES (' + ','.join('?' for _ in range(15)) + ')', rows)
        db.execute('INSERT INTO forum_posts(id,user_id,title,content,created_at) VALUES (1,1,?,?,?)', ('Test discussion', 'Test content', '2026-10-20 01:03:05'))
        db.execute('INSERT INTO forum_replies(id,post_id,user_id,content,created_at) VALUES (1,1,1,?,?)', ('Test reply', '2026-10-20 01:03:05'))
        db.commit()
        db.close()
        self.client = module.app.test_client()

    def login(self):
        return self.client.post('/login', data={'identity': 'existing@example.invalid', 'password': 'TestOnly!2026'})

    def code_record(self, email='new@example.invalid', code='123456', expired=False):
        db = module.get_db()
        sent = module.utc_now() - timedelta(minutes=10)
        expires = module.utc_now() + timedelta(minutes=-1 if expired else 5)
        db.execute('INSERT OR REPLACE INTO email_codes(email,code_hash,expires_at,sent_at,attempts) VALUES (?,?,?,?,0)',
                   (email, generate_password_hash(code), expires.isoformat(), sent.isoformat()))
        db.commit()
        db.close()

    def register(self, code='123456'):
        return self.client.post('/register', data={'username':'NewUser', 'email':'new@example.invalid', 'code':code,
                                                   'password':'AnotherTest!2026', 'confirm_password':'AnotherTest!2026'})

    def test_01_protected_routes_redirect(self):
        for route in ['/home','/library','/season','/calendar','/search','/forum','/anime/1','/forum/new']:
            with self.subTest(route=route): self.assertEqual(self.client.get(route).status_code, 302)

    def test_02_login_by_email(self):
        self.assertTrue(self.login().location.endswith('/home'))
        self.assertEqual(self.client.get('/home').status_code, 200)

    def test_03_login_by_username(self):
        response = self.client.post('/login', data={'identity':'TestUser','password':'TestOnly!2026'})
        self.assertTrue(response.location.endswith('/home'))

    def test_04_wrong_password_rejected(self):
        self.client.post('/login', data={'identity':'TestUser','password':'wrong'})
        self.assertEqual(self.client.get('/home').status_code, 302)

    def test_05_logout_clears_session(self):
        self.login(); self.client.get('/logout')
        self.assertEqual(self.client.get('/library').status_code, 302)

    def test_06_main_pages_and_classic_entry(self):
        self.login()
        for route in ['/home','/library','/season','/calendar','/search','/forum','/forum/new','/anime/1','/forum/post/1']:
            with self.subTest(route=route): self.assertEqual(self.client.get(route).status_code, 200)
        self.assertIn('经典，值得一看再看', self.client.get('/home').text)

    def test_07_search_chinese(self):
        self.login()
        for route in ['/search?q=进击','/library?q=进击']:
            with self.subTest(route=route):
                text = self.client.get(route).text
                self.assertIn('进击的巨人', text)
                self.assertNotIn('查看 火影忍者 的详情', text)

    def test_08_search_english(self):
        self.login()
        self.assertIn('火影忍者', self.client.get('/library?q=Naruto').text)

    def test_09_classic_and_status_filters(self):
        self.login()
        text = self.client.get('/library?scope=classics&status=finished&sort=score').text
        self.assertIn('找到 2 部作品', text)
        self.assertNotIn('查看 Current Show 的详情', text)

    def test_10_pagination_and_parameter_validation(self):
        self.login()
        self.assertIn('第 2 / 2 页', self.client.get('/library?page=999').text)
        self.assertIn('第 1 / 2 页', self.client.get('/library?page=-3&sort=INVALID&status=INVALID').text)

    def test_11_search_treats_sql_as_text(self):
        self.login()
        response = self.client.get('/library', query_string={'q': "' OR 1=1 --"})
        self.assertEqual(response.status_code, 200)
        self.assertIn('找到 0 部作品', response.text)

    def test_12_bilibili_link_uses_chinese_title(self):
        self.login()
        self.assertIn('https://search.bilibili.com/all?keyword=%E8%BF%9B%E5%87%BB%E7%9A%84%E5%B7%A8%E4%BA%BA', self.client.get('/anime/1').text)

    def test_13_review_with_upload(self):
        self.login()
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j1ioAAAAASUVORK5CYII=')
        response = self.client.post('/anime/1', data={'rating':'9','content':'Test review','image':(io.BytesIO(png),'test.png')}, content_type='multipart/form-data')
        self.assertEqual(response.status_code,302)
        db=module.get_db(); row=db.execute('SELECT rating,image FROM reviews').fetchone(); db.close()
        self.assertEqual(row['rating'],9)
        self.assertTrue((Path(module.UPLOAD_FOLDER)/row['image']).is_file())

    def test_14_invalid_rating_not_saved(self):
        self.login(); self.client.post('/anime/1',data={'rating':'99','content':'invalid'})
        db=module.get_db(); count=db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0]; db.close()
        self.assertEqual(count,0)

    def test_15_forum_create_and_reply(self):
        self.login()
        response=self.client.post('/forum/new',data={'title':'New discussion','content':'A new post'})
        self.assertEqual(response.status_code,302)
        detail=response.location
        self.client.post(detail,data={'content':'A new reply'})
        self.assertIn('A new reply',self.client.get(detail).text)

    def test_16_empty_forum_title_rejected(self):
        self.login(); response=self.client.post('/forum/new',data={'title':'','content':'body'})
        self.assertEqual(response.status_code,200)
        db=module.get_db(); count=db.execute('SELECT COUNT(*) FROM forum_posts').fetchone()[0]; db.close()
        self.assertEqual(count,1)

    def test_17_utc_to_beijing_and_cross_day(self):
        self.assertEqual(module.beijing_time('2026-10-20 01:03:05'),'2026-10-20 09:03')
        self.assertEqual(module.beijing_time('2026-10-20 23:00:00'),'2026-10-21 07:00')
        self.assertEqual(module.beijing_time('2026-10-20T09:03:05+08:00'),'2026-10-20 09:03')
        self.login(); text=self.client.get('/forum/post/1').text
        self.assertIn('2026-10-20 09:03',text); self.assertIn('北京时间',text)

    def test_18_invalid_email_rejected(self):
        for value in ['bad',None,123]:
            with self.subTest(value=value): self.assertEqual(self.client.post('/auth/send-code',json={'email':value}).status_code,400)

    def test_19_registered_email_rejected(self):
        self.assertEqual(self.client.post('/auth/send-code',json={'email':'existing@example.invalid'}).status_code,409)

    def test_20_send_stores_hash_not_plain_code(self):
        with patch.object(module,'send_verification_email',return_value={'message_id':'<test@example.invalid>'}) as send:
            response=self.client.post('/auth/send-code',json={'email':'new@example.invalid'})
        self.assertEqual(response.status_code,200)
        db=module.get_db(); row=db.execute('SELECT * FROM email_codes').fetchone(); db.close()
        self.assertNotEqual(row['code_hash'],send.call_args.args[1])
        self.assertTrue(check_password_hash(row['code_hash'],send.call_args.args[1]))

    def test_21_send_cooldown(self):
        with patch.object(module,'send_verification_email',return_value={}):
            self.client.post('/auth/send-code',json={'email':'new@example.invalid'})
            self.assertEqual(self.client.post('/auth/send-code',json={'email':'new@example.invalid'}).status_code,429)

    def test_22_failed_send_preserves_previous_code(self):
        self.code_record()
        with patch.object(module,'send_verification_email',side_effect=OSError('isolated failure')):
            response=self.client.post('/auth/send-code',json={'email':'new@example.invalid'})
        self.assertEqual(response.status_code,503)
        db=module.get_db(); row=db.execute('SELECT code_hash FROM email_codes').fetchone(); locks=db.execute('SELECT COUNT(*) FROM email_send_locks').fetchone()[0]; db.close()
        self.assertTrue(check_password_hash(row['code_hash'],'123456')); self.assertEqual(locks,0)

    def test_23_concurrent_send_has_one_owner(self):
        entered,release=threading.Event(),threading.Event(); results=[]
        def deliver(*args):
            entered.set()
            if not release.wait(5): raise TimeoutError('test synchronization')
            return {}
        def first():
            with module.app.test_client() as client:
                results.append(client.post('/auth/send-code',json={'email':'parallel@example.invalid'}).status_code)
        with patch.object(module,'send_verification_email',side_effect=deliver) as send:
            thread=threading.Thread(target=first); thread.start()
            try:
                self.assertTrue(entered.wait(3))
                self.assertEqual(self.client.post('/auth/send-code',json={'email':'parallel@example.invalid'}).status_code,429)
            finally:
                release.set(); thread.join(5)
            self.assertFalse(thread.is_alive()); self.assertEqual(send.call_count,1)
        self.assertEqual(results,[200])

    def test_24_registration_with_valid_code(self):
        self.code_record(); response=self.register()
        self.assertTrue(response.location.endswith('/home'))
        db=module.get_db(); user=db.execute('SELECT password FROM users WHERE username=?',('NewUser',)).fetchone(); codes=db.execute('SELECT COUNT(*) FROM email_codes').fetchone()[0]; db.close()
        self.assertTrue(check_password_hash(user['password'],'AnotherTest!2026')); self.assertEqual(codes,0)

    def test_25_registration_expired_code(self):
        self.code_record(expired=True); self.assertFalse(self.register().location.endswith('/home'))

    def test_26_registration_wrong_code_counts_attempt(self):
        self.code_record(); self.register('000000')
        db=module.get_db(); attempts=db.execute('SELECT attempts FROM email_codes').fetchone()[0]; db.close()
        self.assertEqual(attempts,1)

    def test_27_templates_compile(self):
        for name in module.app.jinja_env.list_templates():
            with self.subTest(template=name): module.app.jinja_env.get_template(name)

    def test_28_public_seed_has_no_user_tables(self):
        data=json.loads((ROOT/'data/anime_seed.json').read_text(encoding='utf-8'))
        self.assertEqual(len(data),532); self.assertEqual(len({r['id'] for r in data}),532)
        self.assertEqual(sum(r['is_classic'] for r in data),200)
        self.assertFalse(any({'email','password','user_id'} & set(r) for r in data))

    def test_29_demo_initializer_refuses_existing_data(self):
        with self.assertRaises(RuntimeError): setup_demo.initialize_demo(module)
        db=module.get_db(); count=db.execute('SELECT COUNT(*) FROM users').fetchone()[0]; db.close()
        self.assertEqual(count,1)

    def test_30_clean_seed_and_classic_import_are_repeatable(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(module,'DB_PATH',str(Path(directory)/'demo.db')):
            module.init_db(); counts=setup_demo.initialize_demo(module)
            db=module.get_db(); before=dict(db.execute('SELECT id,mal_id FROM anime')); db.close()
            items=json.loads((ROOT/'data/classics-anilist.json').read_text(encoding='utf-8'))
            with patch('sys.stdout',new=io.StringIO()):
                sync_anime.save_anime(items,classic=True); sync_anime.save_anime(items,classic=True)
            db=module.get_db(); after=dict(db.execute('SELECT id,mal_id FROM anime')); reviews=db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0]; db.close()
            self.assertEqual(counts['anime'],532); self.assertEqual(before,after); self.assertEqual(reviews,1)


class FakeSMTP:
    def login(self,*args): return (235,b'ok')
    def send_message(self,message,**kwargs): self.message=message; return {}
    def quit(self): return (221,b'bye')
    def close(self): pass


class MailerTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'SMTP_HOST':'smtp.example.invalid','SMTP_PORT':'465','SMTP_SECURITY':'ssl','SMTP_USERNAME':'sender@example.invalid','SMTP_PASSWORD':'test-only-password','SMTP_FROM_EMAIL':'sender@example.invalid'})
        self.env.start(); self.addCleanup(self.env.stop)

    def test_31_message_headers_and_verified_tls(self):
        smtp=FakeSMTP()
        with patch.object(mailer.smtplib,'SMTP_SSL',return_value=smtp) as factory:
            result=mailer.send_code_email('receiver@example.invalid','123456')
        self.assertEqual(result['status'],'accepted')
        self.assertTrue(smtp.message['Date']); self.assertTrue(smtp.message['Message-ID'])
        self.assertTrue(smtp.message.is_multipart())
        self.assertEqual(factory.call_args.kwargs['context'].verify_mode,ssl.CERT_REQUIRED)
        self.assertTrue(factory.call_args.kwargs['context'].check_hostname)

    def test_32_transient_connect_retry(self):
        with patch.object(mailer.smtplib,'SMTP_SSL',side_effect=[OSError('test'),FakeSMTP()]) as factory, patch.object(mailer.time,'sleep'):
            self.assertEqual(mailer.send_code_email('receiver@example.invalid','123456')['attempts'],2)
            self.assertEqual(factory.call_count,2)

    def test_33_permanent_auth_failure_not_retried(self):
        smtp=FakeSMTP(); smtp.login=lambda *args: (_ for _ in ()).throw(smtplib.SMTPAuthenticationError(535,b'test'))
        with patch.object(mailer.smtplib,'SMTP_SSL',return_value=smtp) as factory:
            with self.assertRaises(smtplib.SMTPAuthenticationError): mailer.send_code_email('receiver@example.invalid','123456')
            self.assertEqual(factory.call_count,1)

    def test_34_uncertain_delivery_not_retried(self):
        smtp=FakeSMTP(); smtp.send_message=lambda *args,**kwargs: (_ for _ in ()).throw(smtplib.SMTPDataError(451,b'test'))
        with patch.object(mailer.smtplib,'SMTP_SSL',return_value=smtp) as factory:
            with self.assertRaises(smtplib.SMTPDataError): mailer.send_code_email('receiver@example.invalid','123456')
            self.assertEqual(factory.call_count,1)

    def test_35_quit_failure_does_not_undo_acceptance(self):
        smtp=FakeSMTP(); smtp.quit=lambda: (_ for _ in ()).throw(OSError('test'))
        with patch.object(mailer.smtplib,'SMTP_SSL',return_value=smtp):
            self.assertEqual(mailer.send_code_email('receiver@example.invalid','123456')['status'],'accepted')

    def test_36_plaintext_configuration_rejected(self):
        with patch.dict(os.environ,{'SMTP_SECURITY':'plain'}):
            with self.assertRaises(ValueError): mailer.smtp_settings()


if __name__=='__main__':
    unittest.main(verbosity=2)
