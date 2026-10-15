"""Explicit local bootstrap and versioned schema migrations. Passwords never enter committed files.
Runs as the database owner (DATABASE_URL); the API itself runs as the non-owner runtime role."""
import os, secrets
from pathlib import Path
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from .db import Base, Migration, User, AgentDefinition
from .identity import hash_password
from .config import settings
from .prompt import AGENT_ID, AGENT_VERSION, INSTRUCTIONS
from . import migrations

def bootstrap():
    cfg=settings()
    engine=create_engine(cfg.database_url, pool_pre_ping=True)
    OwnerSession=sessionmaker(engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    created=[]
    with OwnerSession() as db:
        if not db.get(Migration,1): db.add(Migration(version=1))
        db.commit()
        migrations.apply(db, engine)
        from .seed_defaults import pack_active
        legacy_accounts=[] if pack_active(db) else [('operations','Olivia Operations',['user']),
                ('compliance','Casey Compliance',['builder']),('admin','Alex Admin',['admin']),('auditor','Avery Auditor',['auditor'])]
        for username,name,roles in legacy_accounts:
            if db.scalar(select(User).where(User.username==username)): continue
            password=os.environ.get(f'BOOTSTRAP_{username.upper()}_PASSWORD') or secrets.token_urlsafe(20)
            db.add(User(username=username,name=name,capabilities=roles,password_hash=hash_password(password)))
            created.append(f'{username}: {password}')
        agent=db.get(AgentDefinition,AGENT_ID)
        limits={'max_evidence':cfg.max_evidence,'max_output_tokens':cfg.max_output_tokens}
        sources=['drive','confluence','jira','slack']
        if not agent:
            db.add(AgentDefinition(id=AGENT_ID,version=AGENT_VERSION,instructions=INSTRUCTIONS,
                allowed_sources=sources,model_alias=cfg.model_alias,limits=limits))
        else:
            # The seeded assistant is code-defined; keep the stored row in step with the shipped prompt/limits.
            agent.version=AGENT_VERSION; agent.instructions=INSTRUCTIONS; agent.model_alias=cfg.model_alias; agent.limits=limits
            agent.allowed_sources=sources
        db.commit()
        migrations.roles(db, os.environ.get('APP_RUNTIME_DB_PASSWORD',''), os.environ.get('GRAFANA_DB_PASSWORD',''))
        # Seeded templates, default MCP registration and platform policy (idempotent, code-defined).
        from .seed_defaults import seed_defaults
        seed_defaults(db)
        # Iteration 3: demo directory (titles, groups, memberships), page-access defaults (W4, idempotent).
        try:
            from .admin.defaults import ensure_directory_defaults
        except ImportError:
            ensure_directory_defaults = None
        if ensure_directory_defaults:
            ensure_directory_defaults(db)
            db.commit()
    if created:
        folder=Path('.local'); folder.mkdir(exist_ok=True); os.chmod(folder,0o700)
        file=folder/'demo-accounts.txt'
        with file.open('a') as out:
            os.chmod(file,0o600); out.write('\n'.join(created)+'\n')
        print('Local accounts created. Read credentials privately from .local/demo-accounts.txt.')
    print('Application schema ready (migrations applied).')
if __name__=='__main__': bootstrap()
