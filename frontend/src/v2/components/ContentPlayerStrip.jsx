import { Link } from "react-router-dom";
import { formatCoins } from "../lib/format";

const imageFor = (player) => player.card_image_stored_url || player.card_image_url || player.player_image_url;

export default function ContentPlayerStrip({ items = [], metric }) {
  if (!items.length) return null;
  return <div className="content-player-strip">{items.map((player, index) => {
    const body = <><div className="content-player-art">{imageFor(player) ? <img src={imageFor(player)} alt="" loading="lazy" /> : <span>{player.rating || "—"}</span>}</div><div className="content-player-copy"><strong>{player.display_name}</strong><span>{player.rating || "—"} {player.primary_position || ""}</span><b>{metric ? metric(player) : formatCoins(player.current_bin)}</b></div></>;
    return player.source_card_id ? <Link className="content-player" to={`/players/${player.source_card_id}`} key={`${player.source_card_id}-${index}`}>{body}</Link> : <div className="content-player" key={`${player.player_ea_id}-${index}`}>{body}</div>;
  })}</div>;
}
