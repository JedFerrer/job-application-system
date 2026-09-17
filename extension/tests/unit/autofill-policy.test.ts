import { describe, expect, it } from "vitest";
import { classifyAutofillField, normalizeAutofillLabel } from "../../src/shared/autofill-policy";
import type { AutofillProfile, DetectedField } from "../../src/shared/types";

const profile: AutofillProfile = {
  personal: { first_name: "QA", last_name: "Tester", email: "qa.tester@example.com", phone: "09171234567", city: "Manila", country: "PH" },
  links: { linkedin: "https://linkedin.example/qa", github: "https://github.example/qa", portfolio: "https://portfolio.example/qa", website: "https://qa.example" },
  work: { current_title: "Engineer", current_company: "Acme", years_experience: 0, expected_salary: "100000" },
  authorization: { requires_sponsorship: false, authorized_to_work: true }, education: [], experience: [], skills: [],
};

function field(label: string, overrides: Partial<DetectedField> = {}): DetectedField {
  return { elementId: `field-${label}`, tagName: "input", inputType: "text", label, visible: true, selectorPath: `#${label}`, ...overrides };
}

describe("autofill policy", () => {
  it("normalizes only whitespace and trailing required markers", () => {
    expect(normalizeAutofillLabel("  First   Name *: ")).toBe("first name");
    expect(normalizeAutofillLabel("Please confirm your first name")).toBe("please confirm your first name");
  });

  it.each([
    ["First Name*", "personal.first_name"], ["Preferred First Name", "personal.first_name"],
    ["Last Name", "personal.last_name"], ["Surname", "personal.last_name"],
    ["Email", "personal.email"], ["Email Address", "personal.email"],
    ["Phone", "personal.phone"], ["Phone Number", "personal.phone"], ["Telephone", "personal.phone"], ["Mobile", "personal.phone"],
    ["City", "personal.city"], ["Country", "personal.country"], ["LinkedIn Profile", "links.linkedin"],
    ["GitHub URL", "links.github"], ["Portfolio", "links.portfolio"], ["Website URL", "links.website"],
    ["Current Title", "work.current_title"], ["Current Job Title", "work.current_title"],
  ])("maps canonical label %s", (label, profilePath) => {
    expect(classifyAutofillField(field(label), profile)).toMatchObject({ action: "fill", profilePath });
  });

  it.each([
    "Do you have at least 3 years experience advising on talent-related functions?",
    "Years of Experience",
    "Describe your experience with Python",
    "How many years of experience do you have?",
    "Please confirm your email address is still valid for interview scheduling",
    "Is your phone available during business hours?",
    "Do you currently live in this country?",
    "What is your current job title and why are you leaving?",
    "Please provide your LinkedIn if relevant",
  ])("fails closed for custom question %s", (label) => {
    expect(classifyAutofillField(field(label), profile)).toMatchObject({ action: "skip", profilePath: null });
  });

  it("preserves approved autocomplete mappings, including the forced failed select", () => {
    expect(classifyAutofillField(field("Forced failed fill", { tagName: "select", inputType: "select", autocomplete: "given-name", options: ["qa-a"] }), profile)).toMatchObject({ action: "fill", profilePath: "personal.first_name", value: "QA" });
    expect(classifyAutofillField(field("Anything", { autocomplete: "email" }), profile)).toMatchObject({ action: "fill", profilePath: "personal.email" });
    expect(classifyAutofillField(field("Anything", { autocomplete: "tel" }), profile)).toMatchObject({ action: "fill", profilePath: "personal.phone" });
  });

  it("keeps exact review fields and sensitive fields out of safe fill", () => {
    expect(classifyAutofillField(field("Expected Salary"), profile)).toMatchObject({ action: "review", profilePath: "work.expected_salary" });
    expect(classifyAutofillField(field("Requires Sponsorship"), profile)).toMatchObject({ action: "review", profilePath: "authorization.requires_sponsorship" });
    expect(classifyAutofillField(field("Veteran Status", { tagName: "select", inputType: "select" }), profile)).toMatchObject({ action: "skip", profilePath: null });
  });
});
