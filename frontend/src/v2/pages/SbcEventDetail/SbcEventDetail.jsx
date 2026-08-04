// src/v2/pages/SbcEventDetail/SbcEventDetail.jsx
import { useParams } from "react-router-dom";
import { useSbcEvent } from "../../hooks/useSbcEvent";
import { useSbcEventImpact } from "../../hooks/useSbcEventImpact";
import ChallengeBreakdownSection from "./sections/ChallengeBreakdownSection";
import ImpactSection from "./sections/ImpactSection";

export default function SbcEventDetail() {
  const { eventId } = useParams();
  const { data: event, isLoading, error } = useSbcEvent(eventId);
  const { data: impact, error: impactError } = useSbcEventImpact(eventId);

  if (isLoading) {
    return <div role="status" className="p-6 text-sm text-[var(--v2-muted)]">Loading...</div>;
  }

  if (error) {
    const status = error?.response?.status;
    return (
      <div role="alert" className="p-6 text-sm text-[var(--v2-negative)]">
        {status === 404 ? "SBC set not found." : "Something went wrong loading this set."}
      </div>
    );
  }

  return (
    <div className="p-6 max-w-5xl mx-auto flex flex-col gap-6">
      <ChallengeBreakdownSection event={event} />
      <ImpactSection impact={impact} status={impactError?.response?.status} />
    </div>
  );
}
