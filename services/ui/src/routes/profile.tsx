import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Alert, Field, Input, Select, TagInput } from "@/components/ui/form";
import { ApiError } from "@/lib/api";
import { getProfile, putProfile } from "@/lib/endpoints";
import { errorMessage } from "@/lib/errors";
import { COMPUTE, GOALS, ProfileInput, type Profile } from "@/lib/schemas";

const COMPUTE_LABEL: Record<(typeof COMPUTE)[number], string> = {
  laptop: "Laptop only",
  free_gpu: "Free GPU (Colab / Kaggle)",
  paid: "Paid compute",
};
const GOAL_LABEL: Record<(typeof GOALS)[number], string> = {
  startup: "Startup",
  portfolio: "Portfolio piece",
  course_project: "Course project",
};

interface Draft {
  skills: string[];
  hours_per_week: string;
  weeks_available: string;
  team_size: string;
  compute: (typeof COMPUTE)[number];
  goal: (typeof GOALS)[number];
  interests: string[];
}

const EMPTY: Draft = {
  skills: [],
  hours_per_week: "10",
  weeks_available: "8",
  team_size: "1",
  compute: "laptop",
  goal: "course_project",
  interests: [],
};

const whole = (text: string): number => (/^\s*\d+\s*$/.test(text) ? Number(text) : Number.NaN);

function toInput(draft: Draft) {
  return ProfileInput.safeParse({
    ...draft,
    hours_per_week: whole(draft.hours_per_week),
    weeks_available: whole(draft.weeks_available),
    team_size: whole(draft.team_size),
  });
}

function draftFrom(p: Profile | undefined): Draft {
  if (!p) return EMPTY;
  return {
    skills: p.skills,
    hours_per_week: String(p.hours_per_week),
    weeks_available: String(p.weeks_available),
    team_size: String(p.team_size),
    compute: p.compute,
    goal: p.goal,
    interests: p.interests,
  };
}

export function ProfilePage() {
  const query = useQuery({
    queryKey: ["profile"],
    queryFn: getProfile,
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 1,
  });
  const isNew = query.isError && query.error instanceof ApiError && query.error.status === 404;
  if (query.isPending) return <p role="status">Loading your profile…</p>;
  if (query.isError && !isNew) return <Alert>{errorMessage(query.error)}</Alert>;
  return <ProfileForm saved_profile={query.data} />;
}

function ProfileForm({ saved_profile }: { saved_profile: Profile | undefined }) {
  const client = useQueryClient();
  const [draft, setDraft] = useState<Draft>(() => draftFrom(saved_profile));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState(false);

  const save = useMutation({
    mutationFn: putProfile,
    onSuccess: (profile) => {
      client.setQueryData(["profile"], profile);
      setSaved(true);
    },
  });

  function update<K extends keyof Draft>(key: K, value: Draft[K]) {
    setSaved(false);
    setDraft((d) => ({ ...d, [key]: value }));
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    setSaved(false);
    const parsed = toInput(draft);
    if (!parsed.success) {
      const next: Record<string, string> = {};
      for (const issue of parsed.error.issues) {
        const key = String(issue.path[0] ?? "form");
        if (!(key in next)) next[key] = issue.code === "invalid_type" ? "Enter a whole number" : issue.message;
      }
      setErrors(next);
      return;
    }
    setErrors({});
    save.mutate(parsed.data);
  }

  return (
    <section className="max-w-xl space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">Your profile</h1>
        <p className="text-sm text-[var(--muted)]">
          {saved_profile ? `Version ${saved_profile.version}. ` : "Not saved yet. "}
          Changes affect future recommendations, not verdicts you already have.
        </p>
      </div>
      <form onSubmit={submit} noValidate className="space-y-4">
        <Field label="Skills" hint="Press Enter after each one (for example python, react)." error={errors.skills}>
          {(ids) => <TagInput {...ids} value={draft.skills} onChange={(v) => update("skills", v)} max={30} />}
        </Field>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <Field label="Hours per week" error={errors.hours_per_week}>
            {({ id, describedBy, invalid }) => (
              <Input
                id={id}
                inputMode="numeric"
                aria-describedby={describedBy}
                aria-invalid={invalid}
                value={draft.hours_per_week}
                onChange={(e) => update("hours_per_week", e.target.value)}
              />
            )}
          </Field>
          <Field label="Weeks available" error={errors.weeks_available}>
            {({ id, describedBy, invalid }) => (
              <Input
                id={id}
                inputMode="numeric"
                aria-describedby={describedBy}
                aria-invalid={invalid}
                value={draft.weeks_available}
                onChange={(e) => update("weeks_available", e.target.value)}
              />
            )}
          </Field>
          <Field label="Team size" error={errors.team_size}>
            {({ id, describedBy, invalid }) => (
              <Input
                id={id}
                inputMode="numeric"
                aria-describedby={describedBy}
                aria-invalid={invalid}
                value={draft.team_size}
                onChange={(e) => update("team_size", e.target.value)}
              />
            )}
          </Field>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Compute" error={errors.compute}>
            {({ id }) => (
              <Select id={id} value={draft.compute} onChange={(e) => update("compute", e.target.value as Draft["compute"])}>
                {COMPUTE.map((c) => (
                  <option key={c} value={c}>
                    {COMPUTE_LABEL[c]}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Goal" error={errors.goal}>
            {({ id }) => (
              <Select id={id} value={draft.goal} onChange={(e) => update("goal", e.target.value as Draft["goal"])}>
                {GOALS.map((g) => (
                  <option key={g} value={g}>
                    {GOAL_LABEL[g]}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </div>
        <Field label="Interests" hint="Topics you would enjoy working on." error={errors.interests}>
          {(ids) => <TagInput {...ids} value={draft.interests} onChange={(v) => update("interests", v)} max={30} />}
        </Field>
        {save.isError && <Alert>{errorMessage(save.error)}</Alert>}
        {saved && save.data && <Alert tone="info">Saved as version {save.data.version}.</Alert>}
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save profile"}
        </Button>
      </form>
    </section>
  );
}
