"""Explicit local bootstrap and schema migration 1. Passwords never enter committed files."""
import os, secrets
from pathlib import Path
from sqlalchemy import select
from .db import Base, engine, SessionLocal, Migration, User, AgentDefinition
from .identity import hash_password
from .config import settings
from .prompt import AGENT_ID, AGENT_VERSION, INSTRUCTIONS

def bootstrap():
    Base.metadata.create_all(engine)
    created=[]
    with SessionLocal() as db:
        if not db.get(Migration,1): db.add(Migration(version=1))
        for username,name,roles in [('operations','Olivia Operations',['user']),
                ('compliance','Casey Compliance',['builder']),('admin','Alex Admin',['admin']),('auditor','Avery Auditor',['auditor'])]:
            if db.scalar(select(User).where(User.username==username)): continue
            password=os.environ.get(f'BOOTSTRAP_{username.upper()}_PASSWORD') or secrets.token_urlsafe(20)
            db.add(User(username=username,name=name,capabilities=roles,password_hash=hash_password(password)))
            created.append(f'{username}: {password}')
        cfg=settings()
        agent=db.get(AgentDefinition,AGENT_ID)
        limits={'max_evidence':cfg.max_evidence,'max_output_tokens':cfg.max_output_tokens}
        if not agent:
            db.add(AgentDefinition(id=AGENT_ID,version=AGENT_VERSION,instructions=INSTRUCTIONS,
                allowed_sources=['drive','confluence'],model_alias=cfg.model_alias,limits=limits))
        else:
            # The seeded assistant is code-defined; keep the stored row in step with the shipped prompt/limits.
            agent.version=AGENT_VERSION; agent.instructions=INSTRUCTIONS; agent.model_alias=cfg.model_alias; agent.limits=limits
        db.commit()
    if created:
        folder=Path('.local'); folder.mkdir(exist_ok=True); os.chmod(folder,0o700)
        file=folder/'demo-accounts.txt'
        with file.open('a') as out:
            os.chmod(file,0o600); out.write('\n'.join(created)+'\n')
        print('Local accounts created. Read credentials privately from .local/demo-accounts.txt.')
    print('Application schema migration 1 ready.')
if __name__=='__main__': bootstrap()
