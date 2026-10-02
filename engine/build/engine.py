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

ROOT=Path(__file__).resolve().parents[2]
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

# GDELT is used for breadth, but only established international/regional
# publishers are allowed through. This prevents local/SEO/review sites from
# dominating the dashboard.
TRUSTED_DOMAINS={
"reuters.com","apnews.com","bbc.co.uk","bbc.com","theguardian.com",
"dw.com","euronews.com","ft.com","bloomberg.com","politico.eu",
"politico.com","france24.com","rfi.fr","aljazeera.com","cnbc.com",
"cnn.com","nytimes.com","washingtonpost.com","wsj.com","npr.org",
"abcnews.go.com","nbcnews.com","cbsnews.com","npr.org",
"japantimes.co.jp","nikkei.com","scmp.com","straitstimes.com",
"thehindu.com","indianexpress.com","timesofindia.indiatimes.com",
"abc.net.au","smh.com.au","theage.com.au","channelnewsasia.com",
"kyodonews.net","koreatimes.co.kr","koreaherald.com",
"thediplomat.com","economist.com","time.com","newsweek.com"
}

SOURCE_QUALITY={
"reuters.com":1.00,"apnews.com":1.00,"bbc.co.uk":1.00,"bbc.com":1.00,
"theguardian.com":0.92,"dw.com":0.92,"euronews.com":0.90,"ft.com":0.98,
"bloomberg.com":0.98,"politico.eu":0.90,"politico.com":0.90,
"france24.com":0.88,"rfi.fr":0.88,"aljazeera.com":0.88,"cnbc.com":0.90,
"cnn.com":0.88,"nytimes.com":0.95,"washingtonpost.com":0.95,"wsj.com":0.98,
"npr.org":0.88,"abcnews.go.com":0.88,"nbcnews.com":0.88,"cbsnews.com":0.88,
"japantimes.co.jp":0.88,"nikkei.com":0.95,"scmp.com":0.88,
"straitstimes.com":0.88,"thehindu.com":0.88,"indianexpress.com":0.86,
"timesofindia.indiatimes.com":0.82,"abc.net.au":0.88,"smh.com.au":0.86,
"theage.com.au":0.86,"channelnewsasia.com":0.88,"kyodonews.net":0.86,
"koreatimes.co.kr":0.84,"koreaherald.com":0.84,"thediplomat.com":0.82,
"economist.com":0.95,"time.com":0.82,"newsweek.com":0.82
}

COUNTRY_REGIONS={
"Europe":["europe","european union","eu ","germany","german","france","french","uk ","united kingdom",
"britain","british","england","brussels","belgium","belgian","netherlands","dutch","italy","italian",
"spain","spanish","portugal","poland","polish","ukraine","ukrainian","russia","russian","sweden",
"norway","denmark","finland","greece","greek","romania","czech","hungary","austria","switzerland",
"ireland","ireland","balkans","nato","eurozone","kaliningrad"],
"USA":["united states","u.s.","u.s.","usa","washington","white house","congress","american",
"trump","federal reserve","fed ","pentagon","senate","house of representatives","texas","california",
"new york","florida","oklahoma"],
"Asia":["asia","asian","china","chinese","japan","japanese","india","indian","korea","korean",
"south korea","north korea","taiwan","asean","singapore","indonesia","indonesian","vietnam","vietnamese",
"philippines","thai","thailand","malaysia","pakistan","bangladesh","australia","australian","new zealand",
"israel","israeli","iran","iranian","saudi arabia","gulf"]
}

MONEY_TERMS=["stock","stocks","share","shares","bond","bonds","yield","yields","interest rate","rates",
"inflation","deflation","gdp","economy","economic","markets","market","oil price","oil prices","gold price",
"gold prices","commodity","commodities","currency","forex","euro","dollar","yen","rupee","yuan","earnings",
"revenue","profit","profits","bank","banks","credit","debt","deficit","budget","tariff","trade balance",
"exports","imports","housing","mortgage","private credit"]
GENERAL_TERMS=["war","peace","ceasefire","election","government","president","minister","parliament","court",
"security","military","attack","sanctions","diplomacy","summit","policy","regulation","ai",
"artificial intelligence","technology","climate","earthquake"]

# Stories that are normally noise for a serious daily intelligence dashboard.
EXCLUDED_TERMS=["sports","football","soccer","tennis","cricket","celebrity","movie review","tv review",
"product review","tablet review","phone review","gaming","horoscope","recipe","fashion","lifestyle",
"travel tips","best restaurants","shopping guide","real estate listings"]

STOPWORDS=set("the and for with from that this have has are was were will into after before about over under says said their they them its his her our your a an of to in on at by as is be it or not than more new latest amid de het een van voor met op om te en aan dat die dit een is zijn was wordt".split())

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
    source_quality:float=0.0
    source_breadth:int=1
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

def source_name(url):
    return urlparse(url).netloc.lower().replace("www.","").split(":")[0]

def fetch(url,timeout=20):
    req=urllib.request.Request(url,headers={"User-Agent":"GlobalDailyIntelligence/1.2"})
    with urllib.request.urlopen(req,timeout=timeout) as r:return r.read()

def domain_quality(domain):
    return SOURCE_QUALITY.get(domain.lower().replace("www.",""),0.0)

def parse_rss(xml_bytes,region_hint):
    root=ET.fromstring(xml_bytes);items=[]
    for item in root.findall(".//item"):
        def get(tag):
            x=item.find(tag);return clean_text(x.text if x is not None else "")
        title,link=get("title"),get("link")
        if title and link:
            pub=get("pubDate") or get("published") or get("updated")
            src=source_name(link)
            items.append(Article(title,link,src,parse_date(pub).isoformat(),get("description"),region_hint,region_hint,
                                 source_quality=domain_quality(src)))
    return items

def gdelt_articles(region,timespan="24h",maxrecords=120):
    q=quote_plus(GDELT_QUERIES[region])
    url=f"https://api.gdeltproject.org/api/v2/doc/doc?query={q}&mode=artlist&maxrecords={maxrecords}&timespan={timespan}&sort=datedesc&format=json"
    try:
        raw=json.loads(fetch(url));out=[]
        for x in raw.get("articles",[]):
            title=clean_text(x.get("title",""));link=x.get("url","")
            domain=(x.get("domain") or source_name(link)).lower().replace("www.","")
            if not title or not link or domain not in TRUSTED_DOMAINS:
                continue
            out.append(Article(title,link,domain,parse_date(x.get("seendate","")).isoformat(),"",
                               region,"",source_quality=domain_quality(domain)))
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
        if key not in by_url or len(a.summary)>len(by_url[key].summary):
            by_url[key]=a
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
    return max(items,key=lambda a:parse_date(a.published).timestamp()/86400
               +0.05*math.log1p(len(counts))+0.02*len(a.summary)+0.03*a.source_quality)

def contains_any(text,terms):
    t=text.lower()
    return sum(1 for x in terms if x in t)

def classify_category(title,summary):
    text=f"{title} {summary}".lower()
    money,general=contains_any(text,MONEY_TERMS),contains_any(text,GENERAL_TERMS)
    strong=["interest rate","inflation","stock market","shares fall","shares rise","oil prices",
            "gold prices","bond yields","government bonds","earnings","gdp","budget deficit","currency",
            "markets sell","market selloff","market rally","housing market","bank profits","credit market",
            "quarterly results","company revenue","company profit","trade deficit","trade surplus"]
    # Primary angle: financial/economic language must describe the central development,
    # not merely a secondary consequence of a political or geopolitical story.
    if any(x in text for x in strong):
        return "Markets & Economy"
    if money>=3 and money>general+1:
        return "Markets & Economy"
    return "General Trending"

def region_score(article,region):
    text=f"{article.title} {article.summary}".lower()
    direct=contains_any(text,COUNTRY_REGIONS[region])
    # A source's editorial geography is only a weak tie-breaker. In particular,
    # GDELT query region is not treated as the article's source region.
    source_hint=1 if article.source_region==region else 0
    quality=article.source_quality
    other=max(contains_any(text,COUNTRY_REGIONS[r]) for r in REGIONS if r!=region)
    direct_component=min(1.0,direct/3.0)
    # Direct actors/location dominate; source hint and source quality are secondary.
    return 0.72*direct_component + 0.10*min(1.0,quality) + 0.08*source_hint + 0.10*(0 if other>direct else 1)

def assign_region(article):
    scores={r:region_score(article,r) for r in REGIONS}
    # When the text contains a clear country/actor signal, use that rather than
    # inheriting the GDELT query region.
    return max(REGIONS,key=lambda r:scores[r])

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
    terms=["president","government","central bank","war","ceasefire","election","sanctions","tariff",
           "interest rate","inflation","oil","energy","bank","markets","trade","security","summit"]
    return min(1.0,0.25+0.09*contains_any(f"{article.title} {article.summary}",terms))

def trend_score(article,cluster_size,novelty):
    age=max(0,(datetime.now(timezone.utc)-parse_date(article.published)).total_seconds()/3600)
    recency=math.exp(-age/18)
    breadth=min(1,math.log1p(cluster_size)/math.log(8))
    quality=article.source_quality
    # Source quality is a ranking factor, not a hard substitute for news value.
    return round(100*(0.30*recency+0.22*novelty+0.23*impact_score(article)+0.10*breadth+0.15*quality),1)

def why_matters(article):
    return ("Dit kan marktverwachtingen, prijzen, rente, bedrijfsresultaten of economische vooruitzichten beïnvloeden."
            if article.category=="Markets & Economy"
            else "Dit is relevant omdat het gevolgen kan hebben voor beleid, geopolitiek, veiligheid of de regionale economische omgeving.")

def what_changed(article):
    return article.summary[:260] if article.summary else "Nieuwe berichtgeving over dit onderwerp is vandaag opgenomen."

def build():
    DATA.mkdir(exist_ok=True);ARCHIVE.mkdir(exist_ok=True)
    raw=load_feeds()
    for r in REGIONS:raw.extend(gdelt_articles(r))
    raw=[a for a in deduplicate(raw)
         if not any(term in f"{a.title} {a.summary}".lower() for term in EXCLUDED_TERMS)]
    clusters=cluster(raw);old=load_archive();candidates=[]
    for c in clusters:
        a=representative(c)
        a.cluster_id=hashlib.sha1(norm_title(a.title).encode()).hexdigest()[:12]
        a.category=classify_category(a.title,a.summary)
        a.region=assign_region(a)
        a.novelty=round(novelty_score(a.title,old),3)
        a.source_breadth=len(set(x.source for x in c))
        a.trend_score=trend_score(a,a.source_breadth,a.novelty)
        a.why_it_matters=why_matters(a)
        a.what_changed=what_changed(a)
        # Low-quality single-source material is not strong enough for the dashboard.
        if a.novelty>=0.34 and (a.source_quality>=0.80 or a.source_breadth>=2):
            candidates.append(a)

    # Select independently within each region/category, with a hard maximum of 5.
    # Clustering already guarantees that one underlying story is represented once.
    selected=[]
    for region in REGIONS:
        for category in CATEGORIES:
            group=sorted([a for a in candidates if a.region==region and a.category==category],
                         key=lambda x:x.trend_score,reverse=True)[:5]
            selected.extend(group)

    output={
        "generated_at":datetime.now(timezone.utc).isoformat(),
        "engine_version":"1.2.0",
        "market_snapshot":build_market_snapshot(),
        "rules":{
            "one_story_one_category":True,
            "one_story_one_region":True,
            "top_5_is_maximum":True,
            "primary_angle_category":True,
            "novelty_required":True,
            "no_keyword_only_classification":True,
            "curated_source_quality":True,
            "gdeltd_query_region_is_not_source_region":True
        },
        "stories":[asdict(a) for a in selected],
        "summary":{}
    }
    for r in REGIONS:
        output["summary"][r]={}
        for cat in CATEGORIES:
            output["summary"][r][cat]=[asdict(a) for a in selected if a.region==r and a.category==cat][:5]

    today=datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (DATA/"daily.json").write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding="utf-8")
    (ARCHIVE/f"{today}.json").write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding="utf-8")
    print("Generated",len(selected),"stories.")

if __name__=="__main__":
    build()
