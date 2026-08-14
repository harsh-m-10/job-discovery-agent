"use client";

import { useState } from "react";
import JobCard, { type CardJob } from "./JobCard";

export default function QueueList({
  jobs,
  secret,
}: {
  jobs: CardJob[];
  secret: string;
}) {
  const [toast, setToast] = useState<string | null>(null);

  function announce(label: string) {
    setToast(label);
    setTimeout(() => setToast(null), 1900);
  }

  return (
    <>
      {jobs.map((job) => (
        <JobCard key={job.job_id} job={job} secret={secret} onDone={announce} />
      ))}
      {toast && (
        <div className="toast" role="status">
          {toast} ✓
        </div>
      )}
    </>
  );
}
