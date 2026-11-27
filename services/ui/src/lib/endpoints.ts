import { apiFetch } from "./api";
import { LoginResponse, Profile, ProfileInput, RegisterResponse } from "./schemas";

export const login = (email: string, password: string) =>
  apiFetch("/auth/login", LoginResponse, { method: "POST", json: { email, password } });

export const register = (email: string, password: string) =>
  apiFetch("/auth/register", RegisterResponse, { method: "POST", json: { email, password } });

export const getProfile = () => apiFetch("/profile", Profile);

export const putProfile = (profile: ProfileInput) =>
  apiFetch("/profile", Profile, { method: "PUT", json: profile });
