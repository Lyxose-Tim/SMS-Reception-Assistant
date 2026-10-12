import { DEFAULT_BOOKING_FIELDS, type BusinessKnowledge } from './types';

/** Fictional demo business. Never contains real customer data. */
export function demoKnowledge(): BusinessKnowledge {
  return {
    name: 'Harbor & Home Cleaning',
    profile: {
      about: 'Locally owned home cleaning service. Every booking is a request until the owner confirms it.',
      hours: 'Monday–Friday 8:00 AM–6:00 PM; Saturday 9:00 AM–1:00 PM; closed Sunday',
      serviceAreas: ['Oakwood', 'Riverside', 'Maple Heights'],
      services: [
        { name: 'Standard cleaning', description: 'Routine cleaning of kitchens, bathrooms and living areas.', pricing: 'Starting at $120' },
        { name: 'Deep cleaning', description: 'Detailed cleaning of kitchens, bathrooms and high-touch areas.', pricing: 'Starting at $220' },
        { name: 'Move-out cleaning', description: 'Cleaning for an empty property at the end of a tenancy.', pricing: 'Quote after reviewing property details' },
      ],
      priceNote: 'Starting prices are estimates; the owner confirms the final quote.',
      contactEmail: 'hello@harborclean.example',
      contactPhone: '+1 202-555-0190',
      address: '',
    },
    faqs: [
      { id: 'faq-supplies', question: 'Do you bring your own cleaning supplies?', answer: 'Yes. We bring all supplies and equipment. Let us know about any product preferences.', enabled: true },
      { id: 'faq-pets', question: 'Do you allow pets at home during cleaning?', answer: 'Absolutely. Please tell us about your pets when you book so the team is prepared.', enabled: true },
      { id: 'faq-cancel', question: 'What is your cancellation policy?', answer: 'Please cancel or reschedule at least 24 hours before your appointment.', enabled: true },
      { id: 'faq-insured', question: 'Are your cleaners insured?', answer: 'Yes, our team is insured and background-checked.', enabled: true },
      { id: 'faq-payment', question: 'Which payment methods do you accept?', answer: 'We accept credit cards and bank transfer after the service is completed.', enabled: true },
    ],
    bookingFields: DEFAULT_BOOKING_FIELDS.map((f) => ({ ...f, label: { ...f.label } })),
  };
}
