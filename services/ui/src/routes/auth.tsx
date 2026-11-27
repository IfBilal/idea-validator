import { useMutation } from "@tanstack/react-query";
import { Link, useRouter } from "@tanstack/react-router";
import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Alert, Field, Input } from "@/components/ui/form";
import { safeRedirect, setSession } from "@/lib/auth";
import { login, register } from "@/lib/endpoints";
import { errorMessage } from "@/lib/errors";
import { EMAIL, PASSWORD } from "@/lib/schemas";

interface Errors {
  email?: string;
  password?: string;
}

function validate(email: string, password: string, checkLength: boolean): Errors {
  const errors: Errors = {};
  const e = EMAIL.safeParse(email);
  if (!e.success) errors.email = e.error.issues[0]?.message;
  if (checkLength) {
    const p = PASSWORD.safeParse(password);
    if (!p.success) errors.password = p.error.issues[0]?.message;
  } else if (password === "") {
    errors.password = "Enter your password";
  }
  return errors;
}

export function LoginPage({ redirectTo }: { redirectTo?: string }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<Errors>({});
  const mutation = useMutation({
    mutationFn: () => login(email.trim(), password),
    onSuccess: (reply) => {
      setSession(reply.access_token, reply.expires_in);
      router.history.push(safeRedirect(redirectTo));
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    const found = validate(email, password, false);
    setErrors(found);
    if (Object.keys(found).length === 0) mutation.mutate();
  }

  return (
    <section className="mx-auto max-w-sm space-y-4">
      <h1 className="text-2xl font-semibold">Log in</h1>
      <form onSubmit={submit} noValidate className="space-y-4">
        <Field label="Email" error={errors.email}>
          {({ id, describedBy, invalid }) => (
            <Input
              id={id}
              type="email"
              autoComplete="email"
              aria-describedby={describedBy}
              aria-invalid={invalid}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          )}
        </Field>
        <Field label="Password" error={errors.password}>
          {({ id, describedBy, invalid }) => (
            <Input
              id={id}
              type="password"
              autoComplete="current-password"
              aria-describedby={describedBy}
              aria-invalid={invalid}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          )}
        </Field>
        {mutation.isError && <Alert>{errorMessage(mutation.error)}</Alert>}
        <Button type="submit" disabled={mutation.isPending} className="w-full">
          {mutation.isPending ? "Logging in…" : "Log in"}
        </Button>
      </form>
      <p className="text-sm text-[var(--muted)]">
        New here?{" "}
        <Link to="/register" className="underline">
          Create an account
        </Link>
      </p>
    </section>
  );
}

export function RegisterPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<Errors>({});
  const mutation = useMutation({
    mutationFn: async () => {
      await register(email.trim(), password);
      return login(email.trim(), password); // straight into the app after registering
    },
    onSuccess: (reply) => {
      setSession(reply.access_token, reply.expires_in);
      router.history.push("/profile");
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    const found = validate(email, password, true);
    setErrors(found);
    if (Object.keys(found).length === 0) mutation.mutate();
  }

  return (
    <section className="mx-auto max-w-sm space-y-4">
      <h1 className="text-2xl font-semibold">Create your account</h1>
      <form onSubmit={submit} noValidate className="space-y-4">
        <Field label="Email" error={errors.email}>
          {({ id, describedBy, invalid }) => (
            <Input
              id={id}
              type="email"
              autoComplete="email"
              aria-describedby={describedBy}
              aria-invalid={invalid}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          )}
        </Field>
        <Field label="Password" hint="At least 10 characters." error={errors.password}>
          {({ id, describedBy, invalid }) => (
            <Input
              id={id}
              type="password"
              autoComplete="new-password"
              aria-describedby={describedBy}
              aria-invalid={invalid}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          )}
        </Field>
        {mutation.isError && <Alert>{errorMessage(mutation.error)}</Alert>}
        <Button type="submit" disabled={mutation.isPending} className="w-full">
          {mutation.isPending ? "Creating account…" : "Create account"}
        </Button>
      </form>
      <p className="text-sm text-[var(--muted)]">
        Already registered?{" "}
        <Link to="/login" className="underline">
          Log in
        </Link>
      </p>
    </section>
  );
}
