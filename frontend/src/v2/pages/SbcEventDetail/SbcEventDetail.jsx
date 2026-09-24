// src/v2/pages/SbcEventDetail/SbcEventDetail.jsx
import { useParams } from "react-router-dom";
import { Link } from "react-router-dom";
import { ArrowLeft, ExternalLink } from "lucide-react";
import { useSbcEvent } from "../../hooks/useSbcEvent";
import { useSbcEventImpact } from "../../hooks/useSbcEventImpact";
import ChallengeBreakdownSection from "./sections/ChallengeBreakdownSection";
import ImpactSection from "./sections/ImpactSection";
import "../content-intelligence.css";

export default function SbcEventDetail() {
  const { eventId } = useParams();
  const { data: event, isLoading, error } = useSbcEvent(eventId);
  const { data: impact, error: impactError } = useSbcEventImpact(eventId);

  if (isLoading) {
    return <main className="content-page"><div role="status" className="content-loading">Loading SBC solution…</div></main>;
  }

  if (error) {
    const status = error?.response?.status;
    return (
      <main role="alert" className="content-page"><div className="content-error">
        {status === 404 ? "SBC set not found." : "Something went wrong loading this set."}
      </div></main>
    );
  }

  return (
    <main className="content-page">
      <div className="content-detail-nav"><Link to="/sbc"><ArrowLeft size={15}/> SBC Market</Link>{event?.sbc_details?.futgg_url ? <a href={event.sbc_details.futgg_url} target="_blank" rel="noreferrer">FUT.GG source <ExternalLink size={14}/></a> : null}</div>
      <ChallengeBreakdownSection event={event} />
      <ImpactSection impact={impact} status={impactError?.response?.status} />
    </main>
  );
}
