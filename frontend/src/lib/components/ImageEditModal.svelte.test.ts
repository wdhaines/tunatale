/**
 * Tests for ImageEditModal component.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import { makeSRSItemDetail } from "../../test/factories";

vi.mock("$lib/api", () => ({
  api: {
    fetchImageCandidates: vi.fn(),
    setItemImageFromUrl: vi.fn(),
    uploadItemImage: vi.fn(),
    removeItemImage: vi.fn(),
  },
}));

import { api } from "$lib/api";
import ImageEditModal from "./ImageEditModal.svelte";

const mockFetchCandidates = vi.mocked(api.fetchImageCandidates);
const mockSetFromUrl = vi.mocked(api.setItemImageFromUrl);
const mockUpload = vi.mocked(api.uploadItemImage);
const mockRemove = vi.mocked(api.removeItemImage);

describe("ImageEditModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFetchCandidates.mockResolvedValue({
      query: "",
      status: "ok",
      candidates: [],
    });
  });

  it("renders current image when item.image_url is set", () => {
    const item = makeSRSItemDetail({ id: 1, text: "banka", image_url: "/api/media/banka.jpg" });
    const { getByAltText } = render(ImageEditModal, {
      props: { item, onclose: vi.fn(), onupdated: vi.fn() },
    });
    expect(getByAltText("banka")).toBeTruthy();
  });

  it("shows 'No image' when item.image_url is null", () => {
    const item = makeSRSItemDetail({ id: 1, text: "banka", image_url: null });
    const { getByText } = render(ImageEditModal, {
      props: { item, onclose: vi.fn(), onupdated: vi.fn() },
    });
    expect(getByText("No image")).toBeTruthy();
  });

  it("remove button calls api.removeItemImage then onupdated", async () => {
    const onupdated = vi.fn();
    const item = makeSRSItemDetail({ id: 5, text: "kava", image_url: "/img/kava.jpg" });
    mockRemove.mockResolvedValue(makeSRSItemDetail({ id: 5, image_url: null }));

    const { getByText } = render(ImageEditModal, {
      props: { item, onclose: vi.fn(), onupdated },
    });
    await fireEvent.click(getByText("Remove"));
    await waitFor(() => {
      expect(mockRemove).toHaveBeenCalledWith(5);
      expect(onupdated).toHaveBeenCalled();
    });
  });

  it("paste URL + Set calls api.setItemImageFromUrl then onupdated", async () => {
    const onupdated = vi.fn();
    const item = makeSRSItemDetail({ id: 3 });
    mockSetFromUrl.mockResolvedValue(makeSRSItemDetail({ id: 3, image_url: "http://x/new.jpg" }));

    const { getByPlaceholderText, getByText } = render(ImageEditModal, {
      props: { item, onclose: vi.fn(), onupdated },
    });
    const input = getByPlaceholderText(/https:\/\/example\.com/);
    await fireEvent.input(input, { target: { value: "http://x/new.jpg" } });
    await fireEvent.click(getByText("Set"));

    await waitFor(() => {
      expect(mockSetFromUrl).toHaveBeenCalledWith(3, "http://x/new.jpg");
      expect(onupdated).toHaveBeenCalled();
    });
  });

  it("Escape key calls onclose", async () => {
    const onclose = vi.fn();
    const item = makeSRSItemDetail({ id: 1 });
    const { container } = render(ImageEditModal, {
      props: { item, onclose, onupdated: vi.fn() },
    });
    const backdrop = container.querySelector(".backdrop")!;
    await fireEvent.keyDown(backdrop, { key: "Escape" });
    expect(onclose).toHaveBeenCalled();
  });

  it("backdrop click calls onclose", async () => {
    const onclose = vi.fn();
    const item = makeSRSItemDetail({ id: 1 });
    const { container } = render(ImageEditModal, {
      props: { item, onclose, onupdated: vi.fn() },
    });
    const backdrop = container.querySelector(".backdrop")!;
    await fireEvent.click(backdrop);
    expect(onclose).toHaveBeenCalled();
  });

  it("fetches candidates on mount", () => {
    const item = makeSRSItemDetail({ id: 7 });
    render(ImageEditModal, {
      props: { item, onclose: vi.fn(), onupdated: vi.fn() },
    });
    expect(mockFetchCandidates).toHaveBeenCalledWith(7, undefined);
  });

  it("hides candidates section when fetch returns 409 (no key)", async () => {
    mockFetchCandidates.mockRejectedValue(
      new Error("POST /api/srs/items/1/image/candidates: HTTP 409"),
    );
    const item = makeSRSItemDetail({ id: 1 });
    const { queryByText } = render(ImageEditModal, {
      props: { item, onclose: vi.fn(), onupdated: vi.fn() },
    });
    await waitFor(() => {
      expect(queryByText("Pixabay candidates")).toBeNull();
    });
  });

  it("shows candidate grid when candidates returned", async () => {
    mockFetchCandidates.mockResolvedValue({
      query: "water",
      status: "ok",
      candidates: [
        {
          preview_url: "http://x/a.jpg",
          webformat_url: "http://x/b.jpg",
          tags: "water",
          width: 100,
          height: 100,
          likes: 5,
        },
      ],
    });
    const item = makeSRSItemDetail({ id: 2 });
    const { getByRole } = render(ImageEditModal, {
      props: { item, onclose: vi.fn(), onupdated: vi.fn() },
    });
    await waitFor(() => {
      const imgs = getByRole("img", { name: "water" });
      expect(imgs).toBeTruthy();
    });
  });

  it("click candidate calls api.setItemImageFromUrl then onupdated", async () => {
    const onupdated = vi.fn();
    mockFetchCandidates.mockResolvedValue({
      query: "water",
      status: "ok",
      candidates: [
        {
          preview_url: "http://x/a.jpg",
          webformat_url: "http://x/b.jpg",
          tags: "water",
          width: 100,
          height: 100,
          likes: 5,
        },
      ],
    });
    mockSetFromUrl.mockResolvedValue(makeSRSItemDetail({ id: 3, image_url: "http://x/b.jpg" }));
    const item = makeSRSItemDetail({ id: 3 });
    const { getByRole } = render(ImageEditModal, {
      props: { item, onclose: vi.fn(), onupdated },
    });
    const img = await waitFor(() => getByRole("img", { name: "water" }));
    await fireEvent.click(img.closest("button")!);
    await waitFor(() => {
      expect(mockSetFromUrl).toHaveBeenCalledWith(3, "http://x/b.jpg");
      expect(onupdated).toHaveBeenCalled();
    });
  });

  it("shows error state on API failure", async () => {
    mockSetFromUrl.mockRejectedValue(new Error("network boom"));
    const item = makeSRSItemDetail({ id: 4 });
    const { getByPlaceholderText, getByText } = render(ImageEditModal, {
      props: { item, onclose: vi.fn(), onupdated: vi.fn() },
    });
    const input = getByPlaceholderText(/https:\/\/example\.com/);
    await fireEvent.input(input, { target: { value: "http://x/fail.jpg" } });
    await fireEvent.click(getByText("Set"));
    expect(await getByText("network boom")).toBeTruthy();
  });



  it("shows rate-limited message when response status is rate_limited", async () => {
    mockFetchCandidates.mockResolvedValue({
      query: "water",
      status: "rate_limited",
      candidates: [],
    });
    const item = makeSRSItemDetail({ id: 1 });
    const { getByText } = render(ImageEditModal, {
      props: { item, onclose: vi.fn(), onupdated: vi.fn() },
    });
    await waitFor(() => {
      expect(getByText("Rate limited — try again shortly")).toBeTruthy();
    });
  });

  it("shows pixabay unavailable message when response status is api_error", async () => {
    mockFetchCandidates.mockResolvedValue({
      query: "water",
      status: "api_error",
      candidates: [],
    });
    const item = makeSRSItemDetail({ id: 1 });
    const { getByText } = render(ImageEditModal, {
      props: { item, onclose: vi.fn(), onupdated: vi.fn() },
    });
    await waitFor(() => {
      expect(getByText("Pixabay unavailable — try again shortly")).toBeTruthy();
    });
  });

  it("shows generic error for non-409/429 candidate fetch failure", async () => {
    mockFetchCandidates.mockRejectedValue("network down");
    const item = makeSRSItemDetail({ id: 1 });
    const { getByText } = render(ImageEditModal, {
      props: { item, onclose: vi.fn(), onupdated: vi.fn() },
    });
    await waitFor(() => {
      expect(getByText("network down")).toBeTruthy();
    });
  });

  it("shows default error message for empty candidate error", async () => {
    mockFetchCandidates.mockRejectedValue("");
    const item = makeSRSItemDetail({ id: 1 });
    const { getByText } = render(ImageEditModal, {
      props: { item, onclose: vi.fn(), onupdated: vi.fn() },
    });
    await waitFor(() => {
      expect(getByText("Failed to fetch candidates")).toBeTruthy();
    });
  });

  it("file upload calls api.uploadItemImage then onupdated", async () => {
    const onupdated = vi.fn();
    mockUpload.mockResolvedValue(makeSRSItemDetail({ id: 1, image_url: "http://x/uploaded.jpg" }));
    const item = makeSRSItemDetail({ id: 1 });
    const { container } = render(ImageEditModal, {
      props: { item, onclose: vi.fn(), onupdated },
    });
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["dummy"], "photo.jpg", { type: "image/jpeg" });
    Object.defineProperty(fileInput, "files", { value: [file] });
    await fireEvent.change(fileInput);
    await waitFor(() => {
      expect(mockUpload).toHaveBeenCalledWith(1, file);
      expect(onupdated).toHaveBeenCalled();
    });
  });

  it("selectCandidate shows non-Error rejection as string", async () => {
    mockFetchCandidates.mockResolvedValue({
      query: "",
      status: "ok",
      candidates: [
        {
          preview_url: "http://x/a.jpg",
          webformat_url: "http://x/b.jpg",
          tags: "water",
          width: 100,
          height: 100,
          likes: 5,
        },
      ],
    });
    mockSetFromUrl.mockRejectedValue("string set error");
    const item = makeSRSItemDetail({ id: 1 });
    const { getByRole, getByText } = render(ImageEditModal, {
      props: { item, onclose: vi.fn(), onupdated: vi.fn() },
    });
    const img = await waitFor(() => getByRole("img", { name: "water" }));
    await fireEvent.click(img.closest("button")!);
    expect(await getByText("string set error")).toBeTruthy();
  });

  it("removeImage shows non-Error rejection as string", async () => {
    const item = makeSRSItemDetail({ id: 5, text: "kava", image_url: "/img/kava.jpg" });
    mockRemove.mockRejectedValue("string remove error");
    const { getByText } = render(ImageEditModal, {
      props: { item, onclose: vi.fn(), onupdated: vi.fn() },
    });
    await fireEvent.click(getByText("Remove"));
    expect(await getByText("string remove error")).toBeTruthy();
  });

  it("Enter key in search input triggers loadCandidates", async () => {
    const item = makeSRSItemDetail({ id: 1 });
    const { getByPlaceholderText } = render(ImageEditModal, {
      props: { item, onclose: vi.fn(), onupdated: vi.fn() },
    });
    const input = getByPlaceholderText("Search query");
    mockFetchCandidates.mockClear();
    await fireEvent.keyDown(input, { key: "Enter" });
    expect(mockFetchCandidates).toHaveBeenCalled();
  });

  it("Enter key in paste URL input triggers setFromPaste", async () => {
    const item = makeSRSItemDetail({ id: 1 });
    mockSetFromUrl.mockResolvedValue(makeSRSItemDetail({ id: 1, image_url: "http://x/new.jpg" }));
    const { getByPlaceholderText } = render(ImageEditModal, {
      props: { item, onclose: vi.fn(), onupdated: vi.fn() },
    });
    const input = getByPlaceholderText(/https:\/\/example\.com/);
    await fireEvent.input(input, { target: { value: "http://x/new.jpg" } });
    await fireEvent.keyDown(input, { key: "Enter" });
    expect(mockSetFromUrl).toHaveBeenCalled();
  });

  it("modal click does not close (stopPropagation)", async () => {
    const onclose = vi.fn();
    const item = makeSRSItemDetail({ id: 1 });
    const { container } = render(ImageEditModal, {
      props: { item, onclose, onupdated: vi.fn() },
    });
    const modal = container.querySelector(".modal")!;
    await fireEvent.click(modal);
    expect(onclose).not.toHaveBeenCalled();
  });

  it("selectCandidate shows Error.message on rejection", async () => {
    mockFetchCandidates.mockResolvedValue({
      query: "",
      status: "ok",
      candidates: [
        {
          preview_url: "http://x/a.jpg",
          webformat_url: "http://x/b.jpg",
          tags: "water",
          width: 100,
          height: 100,
          likes: 5,
        },
      ],
    });
    mockSetFromUrl.mockRejectedValue(new Error("bad url"));
    const item = makeSRSItemDetail({ id: 1 });
    const { getByRole, getByText } = render(ImageEditModal, {
      props: { item, onclose: vi.fn(), onupdated: vi.fn() },
    });
    const img = await waitFor(() => getByRole("img", { name: "water" }));
    await fireEvent.click(img.closest("button")!);
    expect(await getByText("bad url")).toBeTruthy();
  });

  it("removeImage shows Error.message on rejection", async () => {
    const item = makeSRSItemDetail({ id: 5, text: "kava", image_url: "/img/kava.jpg" });
    mockRemove.mockRejectedValue(new Error("delete failed"));
    const { getByText } = render(ImageEditModal, {
      props: { item, onclose: vi.fn(), onupdated: vi.fn() },
    });
    await fireEvent.click(getByText("Remove"));
    expect(await getByText("delete failed")).toBeTruthy();
  });

  it("file upload shows error on rejection", async () => {
    mockUpload.mockRejectedValue(new Error("upload failed"));
    const item = makeSRSItemDetail({ id: 1 });
    const { container, getByText } = render(ImageEditModal, {
      props: { item, onclose: vi.fn(), onupdated: vi.fn() },
    });
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["dummy"], "photo.jpg", { type: "image/jpeg" });
    Object.defineProperty(fileInput, "files", { value: [file] });
    await fireEvent.change(fileInput);
    expect(await getByText("upload failed")).toBeTruthy();
  });

  it("paste URL with empty value does not call API", async () => {
    const item = makeSRSItemDetail({ id: 1 });
    const { getByText } = render(ImageEditModal, {
      props: { item, onclose: vi.fn(), onupdated: vi.fn() },
    });
    await fireEvent.click(getByText("Set"));
    expect(mockSetFromUrl).not.toHaveBeenCalled();
  });

  it("clicking close button calls onclose", async () => {
    const onclose = vi.fn();
    const item = makeSRSItemDetail({ id: 1 });
    const { getByLabelText } = render(ImageEditModal, {
      props: { item, onclose, onupdated: vi.fn() },
    });
    await fireEvent.click(getByLabelText("Close"));
    expect(onclose).toHaveBeenCalled();
  });

  it("file input change with no file does not call API", async () => {
    const item = makeSRSItemDetail({ id: 1 });
    const { container } = render(ImageEditModal, {
      props: { item, onclose: vi.fn(), onupdated: vi.fn() },
    });
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    Object.defineProperty(fileInput, "files", { value: [] });
    await fireEvent.change(fileInput);
    expect(mockUpload).not.toHaveBeenCalled();
  });

  it("typing in search query input updates the query", async () => {
    const item = makeSRSItemDetail({ id: 1 });
    const { getByPlaceholderText } = render(ImageEditModal, {
      props: { item, onclose: vi.fn(), onupdated: vi.fn() },
    });
    const input = getByPlaceholderText("Search query");
    await fireEvent.input(input, { target: { value: "coffee" } });
    expect((input as HTMLInputElement).value).toBe("coffee");
  });
});
