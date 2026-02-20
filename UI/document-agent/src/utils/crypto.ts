/**
 * Simple obfuscation layer for localStorage data.
 *
 * Uses XOR cipher + Base64 encoding so stored values aren't
 * human-readable in DevTools. This is NOT a substitute for
 * proper server-side session management (HttpOnly cookies),
 * but it raises the bar compared to plain-text JSON.
 */

const SECRET = 'dA$7k!mQ9x#Lp2Wv'

function xorCipher(input: string, key: string): string {
  let result = ''
  for (let i = 0; i < input.length; i++) {
    result += String.fromCharCode(input.charCodeAt(i) ^ key.charCodeAt(i % key.length))
  }
  return result
}

/** Encrypt a plain-text string for storage. */
export function encrypt(plain: string): string {
  const ciphered = xorCipher(plain, SECRET)
  return btoa(ciphered)
}

/** Decrypt a previously encrypted string. Returns `null` on failure. */
export function decrypt(encoded: string): string | null {
  try {
    const ciphered = atob(encoded)
    return xorCipher(ciphered, SECRET)
  } catch {
    return null
  }
}
