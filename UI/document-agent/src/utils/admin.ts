/** Same rule as the server (accent and case insensitive); the server is the one that enforces it. */
export const isAdminDepartment = (department: string) =>
  department
    .normalize('NFD')
    .replace(/\p{M}/gu, '')
    .trim()
    .toLowerCase() === 'tecnologia'
