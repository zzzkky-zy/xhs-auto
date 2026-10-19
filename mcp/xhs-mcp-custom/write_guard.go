package main

import (
	"context"
	"encoding/json"
	"github.com/xpzouying/xiaohongshu-mcp/execution"
	"github.com/xpzouying/xiaohongshu-mcp/xiaohongshu"
)

func (s *XiaohongshuService) PublishContent(ctx context.Context, req *PublishRequest) (*PublishResponse, error) {
	return directContentWrite(s, ctx, "publish_content", req, func(ctx context.Context, raw json.RawMessage) (*PublishResponse, error) {
		var reviewed PublishRequest
		if err := json.Unmarshal(raw, &reviewed); err != nil {
			return nil, err
		}
		ctx = execution.TrackSubmission(ctx)
		return protectedWrite(s, ctx, "publish_content", &reviewed, func() (*PublishResponse, error) { return s.executePublishContent(ctx, &reviewed) })
	})
}

func (s *XiaohongshuService) PublishVideo(ctx context.Context, req *PublishVideoRequest) (*PublishVideoResponse, error) {
	return directContentWrite(s, ctx, "publish_with_video", req, func(ctx context.Context, raw json.RawMessage) (*PublishVideoResponse, error) {
		var reviewed PublishVideoRequest
		if err := json.Unmarshal(raw, &reviewed); err != nil {
			return nil, err
		}
		ctx = execution.TrackSubmission(ctx)
		return protectedWrite(s, ctx, "publish_with_video", &reviewed, func() (*PublishVideoResponse, error) { return s.executePublishVideo(ctx, &reviewed) })
	})
}

func (s *XiaohongshuService) PostCommentToFeed(ctx context.Context, feedID, xsecToken, content string) (*PostCommentResponse, error) {
	payload := map[string]any{"feed_id": feedID, "xsec_token": xsecToken, "content": content}
	return directContentWrite(s, ctx, "post_comment_to_feed", payload, func(ctx context.Context, raw json.RawMessage) (*PostCommentResponse, error) {
		var reviewed PostCommentRequest
		if err := json.Unmarshal(raw, &reviewed); err != nil {
			return nil, err
		}
		ctx = execution.TrackSubmission(ctx)
		return protectedWrite(s, ctx, "post_comment_to_feed", []string{reviewed.FeedID, reviewed.Content}, func() (*PostCommentResponse, error) {
			return s.postCommentToFeed(ctx, reviewed.FeedID, reviewed.XsecToken, reviewed.Content)
		})
	})
}

func (s *XiaohongshuService) ReplyCommentToFeed(ctx context.Context, feedID, xsecToken, commentID, userID, content string) (*ReplyCommentResponse, error) {
	payload := map[string]any{"feed_id": feedID, "xsec_token": xsecToken, "comment_id": commentID, "user_id": userID, "content": content}
	return directContentWrite(s, ctx, "reply_comment_in_feed", payload, func(ctx context.Context, raw json.RawMessage) (*ReplyCommentResponse, error) {
		var reviewed ReplyCommentRequest
		if err := json.Unmarshal(raw, &reviewed); err != nil {
			return nil, err
		}
		ctx = execution.TrackSubmission(ctx)
		return protectedWrite(s, ctx, "reply_comment_in_feed", []string{reviewed.FeedID, reviewed.CommentID, reviewed.UserID, reviewed.Content}, func() (*ReplyCommentResponse, error) {
			return s.replyCommentToFeed(ctx, reviewed.FeedID, reviewed.XsecToken, reviewed.CommentID, reviewed.UserID, reviewed.Content)
		})
	})
}

func (s *XiaohongshuService) LikeFeed(ctx context.Context, feedID, xsecToken string) (*ActionResult, error) {
	return protectedWrite(s, ctx, "like_feed", []string{feedID, "like"}, func() (*ActionResult, error) { return s.likeFeed(ctx, feedID, xsecToken) })
}

func (s *XiaohongshuService) UnlikeFeed(ctx context.Context, feedID, xsecToken string) (*ActionResult, error) {
	return protectedWrite(s, ctx, "like_feed", []string{feedID, "unlike"}, func() (*ActionResult, error) { return s.unlikeFeed(ctx, feedID, xsecToken) })
}

func (s *XiaohongshuService) FavoriteFeed(ctx context.Context, feedID, xsecToken string) (*ActionResult, error) {
	return protectedWrite(s, ctx, "favorite_feed", []string{feedID, "favorite"}, func() (*ActionResult, error) { return s.favoriteFeed(ctx, feedID, xsecToken) })
}

func (s *XiaohongshuService) UnfavoriteFeed(ctx context.Context, feedID, xsecToken string) (*ActionResult, error) {
	return protectedWrite(s, ctx, "favorite_feed", []string{feedID, "unfavorite"}, func() (*ActionResult, error) { return s.unfavoriteFeed(ctx, feedID, xsecToken) })
}

func (s *XiaohongshuService) LikeNotification(ctx context.Context, commentID string, unlike bool) (*xiaohongshu.NotificationLikeResult, error) {
	return protectedWrite(s, ctx, "like_notification", struct {
		CommentID string
		Unlike    bool
	}{commentID, unlike}, func() (*xiaohongshu.NotificationLikeResult, error) { return s.likeNotification(ctx, commentID, unlike) })
}

func (s *XiaohongshuService) ReplyNotification(ctx context.Context, commentID, content string) (*xiaohongshu.NotificationReplyResult, error) {
	return protectedWrite(s, ctx, "reply_notification", []string{commentID, content}, func() (*xiaohongshu.NotificationReplyResult, error) {
		return s.replyNotification(ctx, commentID, content)
	})
}

func (s *XiaohongshuService) ReplyDirectMessage(ctx context.Context, conversationID, name, content string, openOnly bool) (*xiaohongshu.DirectMessageReplyResult, error) {
	if openOnly {
		return s.replyDirectMessage(ctx, conversationID, name, content, openOnly)
	}
	return protectedWrite(s, ctx, "reply_direct_message", []string{conversationID, name, content}, func() (*xiaohongshu.DirectMessageReplyResult, error) {
		return s.replyDirectMessage(ctx, conversationID, name, content, openOnly)
	})
}
