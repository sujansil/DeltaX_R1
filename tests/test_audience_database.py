"""Audience persistence/security against an isolated schema in guarded test PG."""
import asyncio
import json
import os
import sys
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from tests.db_guard import require_disposable_test_database

pytestmark = [pytest.mark.database, pytest.mark.asyncio(loop_scope="session"),
              pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="requires guarded disposable PostgreSQL")]


@pytest_asyncio.fixture(loop_scope="session")
async def ctx():
    from dataclasses import replace
    from app.competition_rules import configured_rules
    require_disposable_test_database()
    from app.db import Base, get_db
    from app.main import app
    from app.models import Admin, Round, Question, QuestionOption
    from app.security import create_token
    from app.timing import utc_now
    schema="audience_"+uuid4().hex
    root=create_async_engine(os.environ["DATABASE_URL"])
    async with root.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine=create_async_engine(os.environ["DATABASE_URL"],connect_args={"server_settings":{"search_path":schema}},pool_size=10,max_overflow=20)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    local=async_sessionmaker(engine,expire_on_commit=False)
    async with local() as db:
        db.add(Admin(username="audience-fixture",password_hash="not-an-account-password"))
        rules=configured_rules()
        rules=replace(rules,r1a=replace(rules.r1a,auto_advance_delay_ms=8000))
        a=Round(code="R1A",status="DRAFT",rules_version=rules.version,rules_snapshot=rules.snapshot_json());b=Round(code="R1B",status="DRAFT")
        db.add_all([a,b]);await db.flush()
        for p in range(1,rules.r1a.question_count+1):
            q=Question(round_code="R1A",position=p,answer_type="MCQ",question_text=f"Question {p}: $2^2$?",question_image_url="/media/fixture.png")
            db.add(q);await db.flush()
            options=[QuestionOption(question_id=q.id,position=n,option_text=str(n),image_url="/media/option.png" if n==4 else None) for n in range(1,5)]
            db.add_all(options);await db.flush();q.correct_option_id=options[3].id
        for p in range(1,5):
            db.add(Question(round_code="R1B",position=p,answer_type="NUMERIC",question_text=f"Relay {p}",correct_numeric=Decimal("1234.567890123456")))
        await db.commit()
    async def session():
        async with local() as db:yield db
    app.dependency_overrides[get_db]=session
    client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test")
    token=create_token("audience-fixture","admin")
    yield {"local":local,"client":client,"headers":{"Authorization":"Bearer "+token},"now":utc_now(),"schema":schema}
    await client.aclose();app.dependency_overrides.clear();await engine.dispose()
    async with root.begin() as conn:await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await root.dispose()


async def test_public_whitelist_preview_live_and_answer_with_no_gameplay_writes(ctx):
    from app.models import Round, LeaderboardSnapshot, AdminAction
    from app.question_reveal import KIND
    from app.audience import Controls, ACTION
    c=ctx["client"]
    initial=(await c.get('/api/public/audience')).json()
    assert initial['view']=='STANDBY' and initial['question'] is None and initial['answer'] is None
    assert 'controls' not in initial and 'review' not in initial
    r=await c.put('/api/admin/audience',headers=ctx['headers'],json={'expected_revision':0,'preview_upcoming':True,'leaderboard_visible':False})
    assert r.status_code==200
    preview=(await c.get('/api/public/audience')).json()
    assert preview['view']=='PREVIEW' and len(preview['question']['options'])==4 and preview['answer'] is None
    assert all(set(o)=={'id','position','text','image_url'} for o in preview['question']['options'])
    async with ctx['local']() as db:
        a=await db.scalar(select(Round).where(Round.code=='R1A'));a.status='LIVE';a.started_at=ctx['now'];a.current_position=1;a.question_started_at=ctx['now'];await db.commit()
    active=(await c.get('/api/public/audience')).json()
    assert active['view']=='QUESTION' and active['answer'] is None
    assert 'correct_option_id' not in json.dumps(active)
    async with ctx['local']() as db:
        a=await db.scalar(select(Round).where(Round.code=='R1A'));a.question_started_at=ctx['now']-timedelta(seconds=30);a.r1a_result_due_at=ctx['now']+timedelta(seconds=8)
        db.add(LeaderboardSnapshot(round_code='R1A',data_json=json.dumps({'kind':KIND,'after_question':1,'rows':[{'rank':1,'team_id':n,'team_name':f'Team {n}','score':100,'password':'PRIVATE-SENTINEL'} for n in range(1,22)]})))
        # Explicitly private payload fields in an unrelated admin action stay private.
        db.add(AdminAction(action='OTHER_ACTION',details='PRIVATE-CREDENTIAL-SENTINEL'))
        await db.commit()
    answer=(await c.get('/api/public/audience')).json()
    assert answer['view']=='ANSWER' and answer['answer']['question_id']==answer['question']['id']
    assert answer['leaderboard'] is None and 'PRIVATE-' not in json.dumps(answer)
    # Read-only public requests never alter authoritative gameplay rows.
    async with ctx['local']() as db:
        a=await db.scalar(select(Round).where(Round.code=='R1A'));before=(a.status,a.current_position,a.question_started_at,a.r1a_result_due_at)
    for _ in range(3):assert (await c.get('/api/public/audience')).status_code==200
    async with ctx['local']() as db:
        a=await db.scalar(select(Round).where(Round.code=='R1A'));assert (a.status,a.current_position,a.question_started_at,a.r1a_result_due_at)==before
        # Force the later stage using persisted timestamps in this test schema only.
        a.r1a_result_due_at=ctx['now']+timedelta(seconds=4)
        settings=Controls(leaderboard_visible=True);db.add(AdminAction(action=ACTION,details=settings.model_dump_json()));await db.commit()
    board=(await c.get('/api/public/audience')).json()
    assert board['view']=='ANSWER_LEADERBOARD' and len(board['leaderboard']['data'])==16
    assert [r['rank'] for r in board['leaderboard']['data']]==[1]*16
    assert 'password' not in json.dumps(board) and 'PRIVATE-' not in json.dumps(board)


async def test_concurrent_admins_have_one_winner_and_fresh_sessions_restore_revision(ctx):
    from app.audience import write_controls, SettingsChange, controls
    from fastapi import HTTPException
    async def write(i):
        async with ctx['local']() as db:
            try:return await write_controls(db,{'sub':'audience-fixture'},SettingsChange(expected_revision=0,preview_upcoming=bool(i%2),leaderboard_visible=bool(i%3)))
            except HTTPException as e:return e.status_code
    results=await asyncio.gather(*(write(i) for i in range(12)))
    assert sum(isinstance(r,dict) for r in results)==1
    assert results.count(409)==11
    winner=next(r for r in results if isinstance(r,dict))
    async with ctx['local']() as db:
        restored,revision=await controls(db)
        assert revision==winner['revision'] and restored.preview_upcoming==winner['preview_upcoming']
    # A new Python process has no in-memory application state or connection pool.
    script="""
import asyncio,json,os,sys
from tests.db_guard import require_disposable_test_database
require_disposable_test_database()
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.audience import controls
async def main():
    e=create_async_engine(os.environ['DATABASE_URL'],connect_args={'server_settings':{'search_path':sys.argv[1]}})
    async with async_sessionmaker(e)() as db:
        value,revision=await controls(db)
        print(json.dumps({'revision':revision,'preview':value.preview_upcoming}))
    await e.dispose()
asyncio.run(main())
"""
    process=await asyncio.create_subprocess_exec(sys.executable,'-B','-c',script,ctx['schema'],stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    out,err=await process.communicate();assert process.returncode==0,err.decode()
    assert json.loads(out)=={'revision':revision,'preview':winner['preview_upcoming']}
    response=await ctx['client'].put('/api/admin/audience',headers=ctx['headers'],json={'expected_revision':revision,'preview_upcoming':False,'leaderboard_visible':True})
    assert response.status_code==200 and response.json()['revision']>revision


async def test_relay_is_embargoed_and_review_restores_and_advances_every_slide(ctx):
    from app.models import Round, LeaderboardSnapshot
    from app.question_reveal import FINAL_KIND
    c=ctx['client'];h=ctx['headers']
    async with ctx['local']() as db:
        b=await db.scalar(select(Round).where(Round.code=='R1B'));b.status='LIVE';b.started_at=ctx['now'];await db.commit()
    assert (await c.post('/api/admin/audience/review',headers=h,json={'expected_revision':0,'action':'start'})).status_code==409
    public=(await c.get('/api/public/audience')).json()
    assert public['view']=='RELAY_STANDBY' and public['question'] is None and public['answer'] is None
    assert '1234.567' not in json.dumps(public)
    async with ctx['local']() as db:
        b=await db.scalar(select(Round).where(Round.code=='R1B'));b.status='ENDED';b.ended_at=ctx['now'];await db.commit()
    assert (await c.get('/api/public/audience')).json()['view']=='REVIEW_READY'
    revision=0
    for step in range(9):
        response=await c.post('/api/admin/audience/review',headers=h,json={'expected_revision':revision,'action':'start' if step==0 else 'next'})
        assert response.status_code==200,response.text
        revision=response.json()['revision']
        # Each GET uses a fresh connection/transaction and restores durable state.
        public=(await c.get('/api/public/audience')).json()
        if step<8:
            assert public['question']['position']==step//2+1
            assert public['view']==('REVIEW_ANSWER' if step%2 else 'REVIEW_QUESTION')
            assert (public['answer']['value']=='1234.567890123456') if step%2 else public['answer'] is None
        else:assert public['view']=='THANK_YOU' and public['leaderboard'] is None
    assert (await c.post('/api/admin/audience/review',headers=h,json={'expected_revision':revision,'action':'next'})).status_code==409
    response=await c.put('/api/admin/audience',headers=h,json={'expected_revision':revision,'preview_upcoming':False,'leaderboard_visible':True})
    assert response.status_code==200
    assert (await c.get('/api/public/audience')).json()['view']=='RESULTS_PENDING'
    async with ctx['local']() as db:
        db.add(LeaderboardSnapshot(round_code='R1B',data_json=json.dumps({'kind':FINAL_KIND,'rows':[{'rank':1,'team_id':1,'team_name':'Fixture','score':4000}]})));await db.commit()
    public=(await c.get('/api/public/audience')).json()
    assert public['view']=='LEADERBOARD' and public['leaderboard']['data'][0]['score']==4000


async def test_admin_auth_boundaries_and_control_validation(ctx):
    from app.security import create_token
    c=ctx['client']
    for path,method in [('/api/admin/audience','GET'),('/api/admin/audience','PUT'),('/api/admin/audience/review','POST')]:
        assert (await c.request(method,path,json={} if method!='GET' else None)).status_code in (401,403)
        assert (await c.request(method,path,headers={'Authorization':'Bearer '+create_token('fixture','team')},json={} if method!='GET' else None)).status_code==403
    assert (await c.put('/api/public/audience',json={})).status_code==405
    response=await c.put('/api/admin/audience',headers=ctx['headers'],json={'expected_revision':0,'preview_upcoming':False,'leaderboard_visible':False,'question_id':12})
    assert response.status_code==422
    assert (await c.get('/api/public/audience?question_id=23&answer=true')).json()['question'] is None
    from app.models import AdminAction
    from app.audience import ACTION
    async with ctx['local']() as db:
        db.add(AdminAction(action=ACTION,details='{"version":99,"private":"PRIVATE-SENTINEL"}'));await db.commit()
    rejected=await c.get('/api/public/audience')
    assert rejected.status_code==503 and 'PRIVATE-SENTINEL' not in rejected.text


async def test_concurrent_review_advances_cannot_skip_the_mandatory_answer(ctx):
    from app.models import Round
    async with ctx['local']() as db:
        b=await db.scalar(select(Round).where(Round.code=='R1B'));b.status='ENDED';b.started_at=ctx['now'];b.ended_at=ctx['now'];await db.commit()
    c=ctx['client'];h=ctx['headers']
    started=await asyncio.gather(*(c.post('/api/admin/audience/review',headers=h,json={'expected_revision':0,'action':'start'}) for _ in range(2)))
    assert sorted(r.status_code for r in started)==[200,409]
    revision=next(r.json()['revision'] for r in started if r.status_code==200)
    advanced=await asyncio.gather(*(c.post('/api/admin/audience/review',headers=h,json={'expected_revision':revision,'action':'next'}) for _ in range(2)))
    assert sorted(r.status_code for r in advanced)==[200,409]
    display=(await c.get('/api/public/audience')).json()
    assert display['view']=='REVIEW_ANSWER' and display['question']['position']==1
