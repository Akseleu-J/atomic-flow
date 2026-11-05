declare module "node:crypto" {
  export function createHash(algorithm: "sha256"): {
    update(text: string): { digest(encoding: "hex"): string };
  };
}
