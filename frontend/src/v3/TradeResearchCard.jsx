import { Link } from "react-router-dom";
import { ArrowUpRight, ShieldCheck } from "lucide-react";
import CoinValue from "../v2/components/CoinValue";
import MarketFreshness from "../v2/components/MarketFreshness";
import { riskLabel, versionLabel } from "../v2/lib/format";
import { buildWhyText } from "../v2/lib/tradeNarrative";

export default function TradeResearchCard({item,priceAgeLimit=180}){
  const cardId=item.card_id??item.source_card_id;
  const confidence=item.confidence_score==null?null:Math.round(Number(item.confidence_score)*100);
  const roi=item.roi_at_max_buy!=null?Number(item.roi_at_max_buy)*100:item.expected_roi!=null?Number(item.expected_roi)*100:null;
  const isImmediate=item.expected_profit_basis!=null&&item.recommended_buy_max!=null&&item.expected_profit_basis!==item.recommended_buy_max;
  return <Link to={`/players/${cardId}`} state={{from:"trade-finder"}} className="v3-research-card">
    <div className="v3-research-top"><span>{item.signal==="watch"?"WATCH CANDIDATE":"TRADE RESEARCH"}</span><span>{confidence==null?"NO SCORE":`${confidence}% CONFIDENCE`}</span></div>
    <div className="v3-research-player">
      <div className="v3-research-art"><img src={item.composited_image_url||item.image_url||"/img/card-placeholder.png"} alt="" loading="lazy"/></div>
      <div className="v3-research-identity"><span className="v3-research-category">{item.rating??"—"} OVR / {item.position||"—"} / {versionLabel(item.rarity)}</span><h2>{item.name||"Unknown player"}</h2><p>{buildWhyText(item)}</p></div>
    </div>
    <div className="v3-research-economics">
      <div><span>{isImmediate?"BUY NOW":"BUY BELOW"}</span><strong><CoinValue value={item.expected_profit_basis??item.recommended_buy_max}/></strong>{isImmediate?<small>Max <CoinValue value={item.recommended_buy_max}/></small>:<small>Entry target</small>}</div>
      <div><span>MIN PROFIT AT MAX BUY</span><strong className="v3-research-profit"><CoinValue value={item.minimum_profit_at_max_buy??item.expected_profit_after_tax} signed/></strong><small>{roi==null?"ROI unavailable":`${roi>0?"+":""}${roi.toFixed(1)}% after tax`}</small></div>
    </div>
    <div className="v3-research-footer"><span><ShieldCheck size={15}/>{riskLabel(item.risk_level)} risk</span>{item.evidence_quality==="estimated"?<span>BIN estimate</span>:null}<MarketFreshness priceAgeSeconds={item.price_age_seconds} checkedAt={item.current_bin_checked_at||item.bin_checked_at||item.current_bin_captured_at} staleThresholdSeconds={priceAgeLimit} compact/><ArrowUpRight size={17}/></div>
  </Link>;
}
