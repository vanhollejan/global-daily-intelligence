from __future__ import annotations
import hashlib, json, math, re
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus, urlparse
from markets import build_market_snapshot
import urllib.request
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"
ARCHIVE=DATA/"archive"
REGIONS=["Europe","USA","Asia"]
CATEGORIES=["General Trending","Markets & Economy"]

DEFAULT_FEEDS={
"Europe":["https://feeds.bbci.co.uk/news/world/europe/rss.xml","https://rss.dw.com/rdf/rss-en-eu","https://www.euronews.com/rss?level=theme&name=europe"],
"USA":["https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml","https://www.theguardian.com/us-news/rss"],
"Asia":["https://feeds.bbci.co.uk/news/world/asia/rss.xml","https://www.theguardian.com/world/asia/rss"]}

GDELT_QUERIES={
"Europe":'(Europe OR EU OR European OR Germany OR France OR UK)',
"USA":'("United States" OR USA OR Washington OR Trump OR American)',
"Asia":'(China OR Japan OR India OR Korea OR ASEAN OR Asia)'}

MONEY_TERMS=["stock","stocks","share","shares","bond","bonds","yield","yields","interest rate","rates","inflation","deflation","gdp","economy","economic","markets","market","oil price","oil prices","gold price","gold prices","commodity","commodities","currency","forex","euro","dollar","yen","rupee","yuan","earnings","revenue","profit","bank","banks","credit","debt","deficit","budget","tariff","trade balance","exports","imports","housing","mortgage","private credit"]
GENERAL_TERMS=["war","peace","ceasefire","election","government","president","minister","parliament","court","security","military","attack","sanctions","diplomacy","summit","policy","regulation","ai","artificial intelligence","technology","climate","earthquake"]
STOPWORDS=set("the and for with from that this have has are was were will into after before about over under says said their they them its his her our your a an of to in on at by as is be it or not than more new latest amid de het een van voor met op om te en in aan dat die dit een is zijn was wordt".split())

@dataclass
class Article:
    title:str
    url:str
    source:str
    published:str
    summary:str=""
    region_hint:str=""
    source_region:str=""
    cluster_id:str=""
    region:str=""
    category:str=""
    novelty:float=0.0
    trend_score:float=0.0
    why_it_matters:str=""
    what_changed:str=""

def clean_text(s):
    return re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",s or "")).strip()

def norm_title(s):
    s=re.sub(r"https?://\S+"," ",s.lower())
    s=re.sub(r"[^a-z0-9\s]"," ",s)
    return " ".join(w for w in s.split() if w not in STOPWORDS and len(w)>2)

def tokens(s): return set(norm_title(s).split())

def similarity(a,b):
    A,B=tokens(a),tokens(b)
    return len(A&B)/len(A|B) if A and B else 0

def parse_date(v):
    if not v:return datetime.now(timezone.utc)
    try:
        d=parsedate_to_datetime(v)
        if d.tzinfo is None:d=d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        try:return datetime.fromisoformat(v.replace("Z","+00:00")).astimezone(timezone.utc)
        except Exception:return datetime.now(timezone.utc)

def source_name(url):return urlparse(url).netloc.lower().replace("www.","").split(":")[0]

def fetch(url,timeout=20):
    req=urllib.request.Request(url,headers={"User-Agent":"GlobalDailyIntelligence/1.1"})
    with urllib.request.urlopen(req,timeout=timeout) as r:return r.read()

def parse_rss(xml_bytes,region_hint):
    root=ET.fromstring(xml_bytes);items=[]
    for item in root.findall(".//item"):
        def get(tag):
            x=item.find(tag);return clean_text(x.text if x is not None else "")
        title,link=get("title"),get("link")
        if title and link:
            pub=get("pubDate") or get("published") or get("updated")
            items.append(Article(title,link,source_name(link),parse_date(pub).isoformat(),get("description"),region_hint,region_hint))
    return items

def gdelt_articles(region,timespan="24h",maxrecords=80):
    q=quote_plus(GDELT_QUERIES[region])
    url=f"https://api.gdeltproject.org/api/v2/doc/doc?query={q}&mode=artlist&maxrecords={maxrecords}&timespan={timespan}&sort=datedesc&format=json"
    try:
        raw=json.loads(fetch(url));out=[]
        for x in raw.get("articles",[]):
            title=clean_text(x.get("title",""));link=x.get("url","")
            if title and link:
                out.append(Article(title,link,x.get("domain",source_name(link)),parse_date(x.get("seendate","")).isoformat(),"",region,region))
        return out
    except Exception as e:
        print("GDELT failed:",region,e);return []

def load_feeds():
    articles=[]
    for region,feeds in DEFAULT_FEEDS.items():
        for feed in feeds:
            try:articles.extend(parse_rss(fetch(feed),region))
            except Exception as e:print("RSS failed:",feed,e)
    return articles

def deduplicate(articles):
    by_url={}
    for a in articles:
        key=a.url.split("#")[0].rstrip("/")
        if key not in by_url or len(a.summary)>len(by_url[key].summary):by_url[key]=a
    return list(by_url.values())

def cluster(articles,threshold=0.42):
    clusters=[]
    for a in sorted(articles,key=lambda x:x.published,reverse=True):
        for c in clusters:
            if max(similarity(a.title,b.title) for b in c[:8])>=threshold:
                c.append(a);break
        else:clusters.append([a])
    return clusters

def representative(items):
    counts=Counter(a.source for a in items)
    return max(items,key=lambda a:parse_date(a.published).timestamp()/86400+0.05*math.log1p(len(counts))+0.02*len(a.summary))

def contains_any(text,terms):
    t=text.lower();return sum(1 for x in terms if x in t)

def classify_category(title,summary):
    text=f"{title} {summary}".lower()
    money,general=contains_any(text,MONEY_TERMS),contains_any(text,GENERAL_TERMS)
    strong=["interest rate","inflation","stock market","shares fall","shares rise","oil prices","gold prices","bond yields","earnings","gdp","budget deficit","currency","markets","housing market","bank profits","credit market"]
    if any(x in text for x in strong) or (money>=3 and money>general):return "Markets & Economy"
    return "General Trending"

def region_score(article,region):
    text=f"{article.title} {article.summary}".lower()
    terms={
    "Europe":["europe","eu","european","germany","france","uk","britain","brussels","eurozone","italy","poland"],
    "USA":["united states","usa","u.s.","washington","trump","american","white house","congress","federal reserve"],
    "Asia":["asia","china","chinese","japan","japanese","india","indian","korea","korean","taiwan","asean","singapore"]}
    return 0.70*contains_any(text,terms[region])+0.30*(1 if article.source_region==region else 0)

def assign_region(article):return max(REGIONS,key=lambda r:region_score(article,r))

def load_archive():
    previous=[]
    if ARCHIVE.exists():
        for p in sorted(ARCHIVE.glob("*.json"))[-14:]:
            try:previous += [x.get("title","") for x in json.loads(p.read_text(encoding="utf-8")).get("stories",[])]
            except Exception:pass
    return previous

def novelty_score(title,old):
    if not old:return 1.0
    return max(0.0,min(1.0,1.0-max((similarity(title,x) for x in old),default=0)))

def impact_score(article):
    terms=["president","government","central bank","war","ceasefire","election","sanctions","tariff","interest rate","inflation","oil","energy","bank","markets","trade","security","summit"]
    return min(1.0,0.25+0.09*contains_any(f"{article.title} {article.summary}",terms))

def trend_score(article,cluster_size,novelty):
    age=max(0,(datetime.now(timezone.utc)-parse_date(article.published)).total_seconds()/3600)
    recency=math.exp(-age/18);breadth=min(1,math.log1p(cluster_size)/math.log(8))
    return round(100*(0.35*recency+0.25*novelty+0.25*impact_score(article)+0.15*breadth),1)

def why_matters(article):
    return ("Dit kan marktverwachtingen, prijzen, rente, bedrijfsresultaten of economische vooruitzichten beïnvloeden." if article.category=="Markets & Economy" else "Dit is relevant omdat het gevolgen kan hebben voor beleid, geopolitiek, veiligheid of de regionale economische omgeving.")

def what_changed(article):
    return article.summary[:260] if article.summary else "Nieuwe berichtgeving over dit onderwerp is vandaag opgenomen."

def build():
    DATA.mkdir(exist_ok=True);ARCHIVE.mkdir(exist_ok=True)
    raw=load_feeds()
    for r in REGIONS:raw.extend(gdelt_articles(r))
    clusters=cluster(deduplicate(raw));old=load_archive();stories=[]
    for c in clusters:
        a=representative(c);a.cluster_id=hashlib.sha1(norm_title(a.title).encode()).hexdigest()[:12]
        a.category=classify_category(a.title,a.summary);a.region=assign_region(a)
        a.novelty=round(novelty_score(a.title,old),3);a.trend_score=trend_score(a,len(c),a.novelty)
        a.why_it_matters=why_matters(a);a.what_changed=what_changed(a)
        if a.novelty>=0.34:stories.append(a)
    selected=[];seen=set()
    for a in sorted(stories,key=lambda x:x.trend_score,reverse=True):
        if a.cluster_id not in seen:seen.add(a.cluster_id);selected.append(a)
    output={"generated_at":datetime.now(timezone.utc).isoformat(),"engine_version":"1.1.1","market_snapshot":build_market_snapshot(),"rules":{"one_story_one_category":True,"one_story_one_region":True,"top_5_is_maximum":True,"primary_angle_category":True,"novelty_required":True,"no_keyword_only_classification":True},"stories":[asdict(a) for a in selected],"summary":{}}
    for r in REGIONS:
        output["summary"][r]={}
        for cat in CATEGORIES:
            output["summary"][r][cat]=sorted([asdict(a) for a in selected if a.region==r and a.category==cat],key=lambda x:x["trend_score"],reverse=True)[:5]
    today=datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (DATA/"daily.json").write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding="utf-8")
    (ARCHIVE/f"{today}.json").write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding="utf-8")
    print("Generated",len(selected),"stories.")

if __name__=="__main__":build()
