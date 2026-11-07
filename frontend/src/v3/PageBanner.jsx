import { Link } from "react-router-dom";
import { ArrowUpRight } from "lucide-react";
export default function PageBanner({kicker,title,description,action,actionTo,issue}){
  return <header className="v3-page-banner">
    <div><span className="v3-page-kicker">{kicker||"EAFC.LIVE / THE TRADING DESK"}</span><h1>{title}</h1>{description?<p>{description}</p>:null}</div>
    {action&&actionTo?<Link className="v3-page-banner-aside" to={actionTo}>{action} <ArrowUpRight size={22}/></Link>:<span className="v3-page-banner-aside" aria-hidden="true">{issue||"// 03"}</span>}
  </header>;
}
