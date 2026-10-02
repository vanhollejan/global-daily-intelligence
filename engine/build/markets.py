from datetime import datetime, timezone
from urllib.parse import quote
from urllib.request import Request, urlopen
import json

MARKETS={
 "Europe":{"STOXX 600":"^STOXX","DAX":"^GDAXI","CAC 40":"^FCHI","FTSE 100":"^FTSE"},
 "USA":{"S&P 500":"^GSPC","Nasdaq 100":"^NDX"},
 "Asia":{"Nikkei 225":"^N225","CSI 300":"000300.SS"}
}

def get_index(symbol):
    try:
        url="https://query1.finance.yahoo.com/v8/finance/chart/"+quote(symbol,safe="")+"?range=1y&interval=1d"
        req=Request(url,headers={"User-Agent":"Mozilla/5.0 GlobalDailyIntelligence/1.1"})
        with urlopen(req,timeout=20) as r:
            result=json.loads(r.read())["chart"]["result"][0]
        meta=result["meta"]
        value=meta.get("regularMarketPrice")
        previous=meta.get("chartPreviousClose")
        day=((value/previous)-1)*100 if value and previous else None
        timestamps=result.get("timestamp",[])
        closes=result.get("indicators",{}).get("quote",[{}])[0].get("close",[])
        year=datetime.now(timezone.utc).year
        jan=int(datetime(year,1,1,tzinfo=timezone.utc).timestamp())
        starts=[c for t,c in zip(timestamps,closes) if t>=jan and c is not None]
        ytd=((value/starts[0])-1)*100 if value and starts else None
        return {"value":value,"day_pct":day,"ytd_pct":ytd,"symbol":symbol}
    except Exception as e:
        return {"value":None,"day_pct":None,"ytd_pct":None,"symbol":symbol,"error":str(e)}

def build_market_snapshot():
    return {region:{name:get_index(symbol) for name,symbol in indices.items()} for region,indices in MARKETS.items()}
