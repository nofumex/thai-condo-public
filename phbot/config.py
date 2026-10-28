from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv
ROOT=Path(__file__).resolve().parent.parent
load_dotenv(ROOT/'.env')
@dataclass(frozen=True)
class Config:
    token:str
    admin_id:int
    db_path:str
    channel_chat:str='@thai_condo'
    contact_label:str='Аренда, консультация и подбор квартиры - @nofumez'
    channel_chat:str='@thai_condo'
    contact_label:str='Аренда, консультация и подбор квартиры - @nofumez'
    @classmethod
    def from_env(cls):
        token=os.getenv('TG_BOT_TOKEN','').strip()
        admin=os.getenv('ADMIN_ID','').strip()
        if not token or not admin.isdigit():
            raise SystemExit('Заполни TG_BOT_TOKEN и ADMIN_ID в .env')
        return cls(token,int(admin),str(ROOT/'data'/'propertyhub.sqlite3'),
                   os.getenv('CHANNEL_CHAT_ID','@thai_condo').strip(),
                   os.getenv('PUBLIC_CONTACT','Аренда, консультация и подбор квартиры - @nofumez').strip())
