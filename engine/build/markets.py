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
        req=Request(url,headers={"User-Agent":"Mozilla/5.0 GlobalDailyIntelligence/1.2"})
        with urlopen(req,timeout=20) as r:
            result=json.loads(r.read())["chart"]["result"][0]

        timestamps=result.get("timestamp",[])
        closes=result.get("indicators",{}).get("quote",[{}])[0].get("close",[])
        points=[(t,c) for t,c in zip(timestamps,closes) if t is not None and c is not None]
        if not points:
            raise ValueError("No daily close data returned")

        # Use daily candles consistently. regularMarketPrice can be an intraday
        # value while chartPreviousClose may refer to a different session.
        last_ts,last_close=points[-1]
        prev_close=points[-2][1] if len(points)>=2 else None
        day=((last_close/prev_close)-1)*100 if prev_close else None

        year=datetime.now(timezone.utc).year
        jan=int(datetime(year,1,1,tzinfo=timezone.utc).timestamp())
        ytd_points=[(t,c) for t,c in points if t>=jan]
        first_ytd=ytd_points[0][1] if ytd_points else None
        ytd=((last_close/first_ytd)-1)*100 if first_ytd else None

        as_of=datetime.fromtimestamp(last_ts,timezone.utc).isoformat()
        return {
            "value":last_close,
            "day_pct":round(day,2) if day is not None else None,
            "ytd_pct":round(ytd,2) if ytd is not None else None,
            "symbol":symbol,
            "as_of":as_of,
            "data_basis":"last available daily close"
        }
    except Exception as e:
        return {"value":None,"day_pct":None,"ytd_pct":None,"symbol":symbol,"error":str(e)}

def build_market_snapshot():
    return {region:{name:get_index(symbol) for name,symbol in indices.items()} for region,indices in MARKETS.items()}
