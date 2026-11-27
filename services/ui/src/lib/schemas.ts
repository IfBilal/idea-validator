import { z } from "zod";

/** Request/response shapes of the gateway endpoints the UI uses (services/gateway/README.md). */
export const LoginResponse = z.object({
  access_token: z.string().min(1),
  token_type: z.literal("bearer"),
  expires_in: z.number().int().positive(),
});

export const RegisterResponse = z.object({ user_id: z.string() });

export const COMPUTE = ["laptop", "free_gpu", "paid"] as const;
export const GOALS = ["startup", "portfolio", "course_project"] as const;

const Tag = z.string().trim().min(1, "Empty entries are not allowed").max(50, "At most 50 characters");

/** What the user edits (the gateway adds user_id and version). Mirrors schemas/student_profile.schema.json. */
export const ProfileInput = z.object({
  skills: z.array(Tag).max(30, "At most 30 skills"),
  hours_per_week: z.number().int("Whole hours only").min(0).max(80, "At most 80 hours a week"),
  weeks_available: z.number().int("Whole weeks only").min(1, "At least 1 week").max(52, "At most 52 weeks"),
  team_size: z.number().int("Whole people only").min(1, "At least 1 person").max(10, "At most 10 people"),
  compute: z.enum(COMPUTE),
  goal: z.enum(GOALS),
  interests: z.array(Tag).max(30, "At most 30 interests"),
});
export type ProfileInput = z.infer<typeof ProfileInput>;

export const Profile = ProfileInput.extend({
  user_id: z.string(),
  version: z.number().int().min(1),
});
export type Profile = z.infer<typeof Profile>;

export const EMAIL = z.string().trim().email("Enter a valid email address").max(254);
export const PASSWORD = z.string().min(10, "Use at least 10 characters").max(200);

export const ValidateAccepted = z.object({
  run_id: z.string(),
  idea_id: z.string(),
  version: z.number().int(),
});

export const Accepted = z.object({}).passthrough();

export const RunAccepted = z.object({ run_id: z.string() }).passthrough();
