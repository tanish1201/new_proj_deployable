const courses = db.getSiblingDB("courses");
const progress = db.getSiblingDB("progress");
const seededAt = new Date();
const instructorId = "900002";
const studentId = "900001";

const demoCourses = [
  {
    _id: ObjectId("650000000000000000000001"),
    title: "Python for Everyday Automation",
    description: "Use Python to automate repetitive work, organize files, and build small tools you can use right away.",
    category: "Programming",
    tags: ["python", "automation", "beginner"],
    instructor_id: instructorId,
    published: true,
    modules: [
      {
        title: "Start with Python",
        lessons: [
          { id: "python-welcome", title: "Welcome and setup" },
          { id: "python-values", title: "Values, variables, and strings" },
          { id: "python-control-flow", title: "Decisions and repetition" },
        ],
      },
      {
        title: "Automate a real task",
        lessons: [
          { id: "python-files", title: "Working with files" },
          { id: "python-organizer", title: "Build a file organizer" },
          { id: "python-next-steps", title: "Test and improve your tool" },
        ],
      },
    ],
    created_at: seededAt,
    updated_at: seededAt,
  },
  {
    _id: ObjectId("650000000000000000000002"),
    title: "Designing Better Web Experiences",
    description: "Learn the fundamentals of user-centered design, accessible interfaces, and prototyping a clear user journey.",
    category: "Design",
    tags: ["design", "ux", "accessibility"],
    instructor_id: instructorId,
    published: true,
    modules: [
      {
        title: "Understand the user",
        lessons: [
          { id: "design-research", title: "Ask better research questions" },
          { id: "design-personas", title: "Map needs and user journeys" },
          { id: "design-content", title: "Write clear interface content" },
        ],
      },
      {
        title: "Design and validate",
        lessons: [
          { id: "design-layout", title: "Create a readable layout" },
          { id: "design-accessibility", title: "Accessibility essentials" },
          { id: "design-prototype", title: "Prototype and get feedback" },
        ],
      },
    ],
    created_at: seededAt,
    updated_at: seededAt,
  },
  {
    _id: ObjectId("650000000000000000000003"),
    title: "Cloud Foundations with Kubernetes",
    description: "Explore containers, Kubernetes building blocks, and the path from a local app to a reliable cloud deployment.",
    category: "Cloud",
    tags: ["cloud", "kubernetes", "containers"],
    instructor_id: instructorId,
    published: true,
    modules: [
      {
        title: "Containers and clusters",
        lessons: [
          { id: "cloud-containers", title: "Why containers?" },
          { id: "cloud-images", title: "Build and tag an image" },
          { id: "cloud-pods", title: "Pods, deployments, and services" },
        ],
      },
      {
        title: "Deploy with confidence",
        lessons: [
          { id: "cloud-config", title: "Configuration and secrets" },
          { id: "cloud-health", title: "Readiness and health checks" },
          { id: "cloud-rollout", title: "Observe and roll out changes" },
        ],
      },
    ],
    created_at: seededAt,
    updated_at: seededAt,
  },
];

for (const course of demoCourses) {
  courses.courses.updateOne(
    { _id: course._id },
    { $set: course },
    { upsert: true },
  );
}

const progressSamples = [
  {
    user_id: studentId,
    course_id: "650000000000000000000001",
    completed_lessons: ["python-welcome", "python-values", "python-control-flow", "python-files", "python-organizer", "python-next-steps"],
    percent: 100,
  },
  {
    user_id: studentId,
    course_id: "650000000000000000000002",
    completed_lessons: ["design-research", "design-personas"],
    percent: 33.33,
  },
];

for (const sample of progressSamples) {
  progress.progress.updateOne(
    { user_id: sample.user_id, course_id: sample.course_id },
    {
      $setOnInsert: {
        ...sample,
        updated_at: seededAt,
      },
    },
    { upsert: true },
  );
}
