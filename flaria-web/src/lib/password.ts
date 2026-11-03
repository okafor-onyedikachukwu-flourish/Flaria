/** Client copy of the server password policy (flaria-core/app/schemas.py: check_password_strength). */
const CLASSES = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/];

export const PASSWORD_HINT = 'At least 12 characters, mixing three of: lowercase, uppercase, numbers, symbols.';

export function passwordProblem(pw: string): string | null {
  if (pw.length < 12) return 'Use at least 12 characters.';
  if (pw.length > 128) return 'Use 128 characters or fewer.';
  if (CLASSES.filter((r) => r.test(pw)).length < 3) return 'Mix at least three of: lowercase, uppercase, numbers, symbols.';
  return null;
}
