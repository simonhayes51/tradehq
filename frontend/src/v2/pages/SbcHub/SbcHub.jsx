// src/v2/pages/SbcHub/SbcHub.jsx
import { useSbcEvents } from "../../hooks/useSbcEvents";
import SbcListSection from "./sections/SbcListSection";

export default function SbcHub() {
  const { data, isLoading, error } = useSbcEvents();

  return (
    <div className="p-6 max-w-5xl mx-auto flex flex-col gap-6">
      <h1 className="text-xl font-semibold">SBC Hub</h1>
      {isLoading ? (
        <p role="status" className="text-sm text-[var(--v2-muted)]">Loading...</p>
      ) : error ? (
        <p role="alert" className="text-sm text-[var(--v2-negative)]">Couldn't load SBC sets right now.</p>
      ) : (
        <SbcListSection events={data?.items} />
      )}
    </div>
  );
}
