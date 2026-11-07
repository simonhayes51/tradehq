import { Link, useLocation } from "react-router-dom";
const AVAILABLE=["/","/opportunities","/trade-finder","/players","/watchlist","/account","/portfolio"];
export default function V4Link({to,...props}){
 const {pathname}=useLocation();
 const inPreview=pathname.startsWith("/preview-v4");
 const supported=typeof to==="string"&&AVAILABLE.some(path=>path==="/"?to==="/":to===path||to.startsWith(path+"/"));
 const target=inPreview&&supported?"/preview-v4"+to:to;
 return <Link to={target} {...props}/>;
}
